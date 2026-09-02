"""Baseline momentum rotation — deliberately boring.

Long-only weekly rotation over a fixed liquid-ETF universe: rank by ~3-month
(LOOKBACK trading day) total return, hold the top N equal-weighted. Exists to
exercise the full pipeline end to end and to be the benchmark later
strategies must beat after costs.

Rebalances are TARGET-SEEKING and idempotent: a rebalance decision sets
target weights, and the strategy emits orders toward those targets once per
completed trading day until the portfolio matches them. Live fills land at
the next open (not instantly, like the replay stub), so the buy leg of a
rotation often cannot pass the gate's cash check until the sell leg has
filled — re-emitting unmet targets the next day is what makes rotations
complete instead of silently dropping their buys for a week.

On restart, cadence and any in-flight targets are restored from the ledger's
decision journal, so a deploy never forces an off-cycle rebalance.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime, timezone

from ..core.events import Event, JournalEntry, MarketBar, OrderIntent, Side
from .base import Strategy
from .params import LOOKBACK, REBALANCE_DAYS, TARGET_GROSS, TOP_N

UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "GLD", "DBC"]
REBALANCE_WEEKDAY = 0   # Monday
MIN_TRADE_VALUE = 200   # skip dust rebalances
BUDGET = 0.75           # fraction of account equity this strategy sizes against

# Off-cycle de-risk: if the researcher's risk appetite falls this far below the
# level used at the last rebalance AND under the floor, sell down to the new
# weights immediately instead of waiting for Monday. Sells only — an appetite
# RISE always waits for the next rebalance (asymmetry is the point).
DERISK_DROP = 0.25
DERISK_FLOOR = 0.6


class BaselineMomentum(Strategy):
    name = "baseline_momentum"

    def __init__(self, portfolio, signals=None, repo=None):
        super().__init__(portfolio)
        self.signals = signals  # SignalStore or None; read-only bounded params
        self.history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=LOOKBACK + 1))
        self.last_close: dict[str, float] = {}
        self._seen_dates: dict[str, date] = {}
        self._last_rebalance: date | None = None
        self._pending_targets: dict[str, float] | None = None  # symbol -> weight
        self._derisk_only = False       # pending targets may only SELL toward
        self._applied_appetite = 1.0    # appetite baked into current targets
        self._last_target_pass: date | None = None
        self._restored = False
        if repo is not None:
            self._restore_from_ledger(repo)

    def _restore_from_ledger(self, repo) -> None:
        decision = repo.last_decision(self.name)
        if not decision:
            return
        ts = datetime.fromisoformat(decision["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - ts).days
        if age_days > REBALANCE_DAYS + 3:
            return
        payload = decision.get("payload") or {}
        winners, weight = payload.get("winners"), payload.get("weight")
        if winners is None or weight is None:
            return
        self._last_rebalance = ts.date()
        self._pending_targets = {s: (weight if s in winners else 0.0) for s in UNIVERSE}
        self._restored = True

    def on_go_live(self) -> None:
        # a restored cadence resumes; only a cold start acts on the first bar
        if not self._restored:
            self._last_rebalance = None

    def on_bar(self, bar: MarketBar) -> list[Event]:
        if bar.symbol not in UNIVERSE:
            return []
        bar_date = bar.bar_ts.date()
        # only record one close per symbol per day (daily bars expected)
        if self._seen_dates.get(bar.symbol) != bar_date:
            self.history[bar.symbol].append(bar.close)
            self._seen_dates[bar.symbol] = bar_date
        self.last_close[bar.symbol] = bar.close

        # act only once all universe symbols have reported today's bar
        if any(self._seen_dates.get(s) != bar_date for s in UNIVERSE):
            return []

        if self._should_rebalance(bar_date):
            ready = [s for s in UNIVERSE if len(self.history[s]) > LOOKBACK]
            if len(ready) == len(UNIVERSE):
                self._last_rebalance = bar_date
                return self._decide(bar_date, bar.bar_ts)

        derisk = self._check_offcycle_derisk(bar_date, bar.bar_ts)
        if derisk:
            return derisk

        # keep pursuing unmet targets from a prior decision, once per day
        if self._pending_targets is not None and self._last_target_pass != bar_date:
            return self._emit_toward_targets(bar_date, bar.bar_ts, journal_progress=True)
        return []

    def _check_offcycle_derisk(self, d: date, bar_ts) -> list[Event]:
        """Between rebalances, a sharp drop in the regime signal sells down to
        the new weights immediately. Sells only; buying back waits for Monday."""
        if self.signals is None:
            return []
        appetite = self.signals.risk_appetite()
        if appetite > DERISK_FLOOR or appetite > self._applied_appetite - DERISK_DROP:
            return []
        old_appetite = self._applied_appetite
        scale = appetite / max(old_appetite, 1e-9)
        equity = self.portfolio.equity
        targets: dict[str, float] = {}
        for symbol in UNIVERSE:
            pos = self.portfolio.positions.get(symbol)
            price = self.last_close.get(symbol, 0.0)
            current_w = (pos.qty * price / equity) if (pos and price > 0 and equity > 0) else 0.0
            targets[symbol] = current_w * scale
        self._pending_targets = targets
        self._derisk_only = True
        self._applied_appetite = appetite
        journal = JournalEntry(
            strategy=self.name, strategy_version=self.version, kind="decision",
            text=(f"{d.isoformat()} OFF-CYCLE DE-RISK: risk appetite fell to {appetite:.2f} "
                  f"from {old_appetite:.2f} at the last rebalance. "
                  f"Selling positions down by {1 - scale:.0%}; re-risking waits for the next rebalance."),
            payload={"appetite": appetite, "scale": scale},
            ts=bar_ts,
        )
        return [journal, *self._emit_toward_targets(d, bar_ts, journal_progress=False)]

    def _should_rebalance(self, d: date) -> bool:
        if self._last_rebalance is None:
            return True
        if (d - self._last_rebalance).days < REBALANCE_DAYS:
            return False
        return d.weekday() == REBALANCE_WEEKDAY or (d - self._last_rebalance).days >= REBALANCE_DAYS + 3

    def _decide(self, d: date, bar_ts) -> list[Event]:
        momentum = {
            s: self.history[s][-1] / self.history[s][0] - 1.0
            for s in UNIVERSE
        }
        ranked = sorted(momentum, key=momentum.get, reverse=True)
        winners = [s for s in ranked[:TOP_N] if momentum[s] > 0]  # absolute-momentum filter
        # cognition-plane regime signal scales gross exposure; the SignalStore
        # clamps it to [0.3, 1.0], so at worst this de-risks — never levers up
        risk_appetite = self.signals.risk_appetite() if self.signals else 1.0
        # BUDGET caps this strategy's share of the account so it can never
        # contend with the earnings sleeve for the gate's gross-exposure cap
        effective_gross = min(TARGET_GROSS, BUDGET) * risk_appetite
        weight = effective_gross / TOP_N if winners else 0.0

        self._pending_targets = {s: (weight if s in winners else 0.0) for s in UNIVERSE}
        self._derisk_only = False
        self._applied_appetite = risk_appetite
        journal = JournalEntry(
            strategy=self.name, strategy_version=self.version, kind="decision",
            text=(
                f"{d.isoformat()} rebalance: hold {winners or 'cash only'} at "
                f"{weight:.1%} each (risk appetite {risk_appetite:.2f}). Momentum ranks: "
                + ", ".join(f"{s} {momentum[s]:+.1%}" for s in ranked)
            ),
            payload={"momentum": momentum, "winners": winners, "weight": weight,
                     "risk_appetite": risk_appetite},
            ts=bar_ts,
        )
        return [journal, *self._emit_toward_targets(d, bar_ts, journal_progress=False)]

    def _emit_toward_targets(self, d: date, bar_ts, journal_progress: bool) -> list[Event]:
        self._last_target_pass = d
        assert self._pending_targets is not None
        equity = self.portfolio.equity
        intents: list[OrderIntent] = []
        for symbol, target_w in self._pending_targets.items():
            price = self.last_close.get(symbol, 0.0)
            if price <= 0:
                continue
            pos = self.portfolio.positions.get(symbol)
            current_value = (pos.qty if pos else 0.0) * price
            delta_value = target_w * equity - current_value
            if abs(delta_value) < MIN_TRADE_VALUE:
                continue
            qty = round(abs(delta_value) / price, 4)
            if qty <= 0:
                continue
            side = Side.BUY if delta_value > 0 else Side.SELL
            if side == Side.BUY and self._derisk_only:
                continue  # de-risk targets may only reduce, never add
            if side == Side.SELL and pos is not None:
                qty = min(qty, pos.qty)  # long-only: never sell more than held
            intents.append(OrderIntent(
                strategy=self.name, strategy_version=self.version,
                symbol=symbol, side=side, qty=qty,
                reasoning=f"toward {target_w:.1%} target (rebalance {self._last_rebalance})",
                ts=bar_ts,
            ))
        if not intents:
            self._pending_targets = None  # converged
            self._derisk_only = False
            return []
        events: list[Event] = []
        if journal_progress:
            events.append(JournalEntry(
                strategy=self.name, strategy_version=self.version, kind="decision",
                text=(f"{d.isoformat()} continuing toward {self._last_rebalance} rebalance targets: "
                      + ", ".join(f"{i.side.value} {i.qty} {i.symbol}" for i in intents)),
                ts=bar_ts,
            ))
        # sells first so cash frees up before buys hit the gate's cash check
        events += sorted(intents, key=lambda e: 0 if e.side == Side.SELL else 1)
        return events
