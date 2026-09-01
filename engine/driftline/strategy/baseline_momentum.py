"""Baseline momentum rotation — deliberately boring.

Long-only weekly rotation over a fixed liquid-ETF universe: rank by ~3-month
(63 trading day) total return, hold the top N equal-weighted, rebalance when
a new decision day arrives. Exists to exercise the full pipeline end to end,
not to be clever; it is the benchmark later strategies must beat after costs.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date

from ..core.events import Event, JournalEntry, MarketBar, OrderIntent, Side
from .base import Strategy

from .params import LOOKBACK, REBALANCE_DAYS, TARGET_GROSS, TOP_N

UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "GLD", "DBC"]
REBALANCE_WEEKDAY = 0  # Monday
MIN_TRADE_VALUE = 200  # skip dust rebalances


class BaselineMomentum(Strategy):
    name = "baseline_momentum"

    def __init__(self, portfolio, signals=None):
        super().__init__(portfolio)
        self.signals = signals  # SignalStore or None; read-only bounded params
        self.history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=LOOKBACK + 1))
        self.last_close: dict[str, float] = {}
        self._seen_dates: dict[str, date] = {}
        self._last_rebalance: date | None = None

    def on_bar(self, bar: MarketBar) -> list[Event]:
        if bar.symbol not in UNIVERSE:
            return []
        bar_date = bar.bar_ts.date()
        # only record one close per symbol per day (daily bars expected)
        if self._seen_dates.get(bar.symbol) != bar_date:
            self.history[bar.symbol].append(bar.close)
            self._seen_dates[bar.symbol] = bar_date
        self.last_close[bar.symbol] = bar.close

        if not self._should_rebalance(bar_date):
            return []
        ready = [s for s in UNIVERSE if len(self.history[s]) > LOOKBACK]
        if len(ready) < len(UNIVERSE):
            return []
        # wait until every symbol has reported today's bar before deciding
        if any(self._seen_dates.get(s) != bar_date for s in UNIVERSE):
            return []

        self._last_rebalance = bar_date
        return self._rebalance(bar_date, bar.bar_ts)

    def on_go_live(self) -> None:
        self._last_rebalance = None  # rebalance on the first fresh daily bar

    def _should_rebalance(self, d: date) -> bool:
        if self._last_rebalance is None:
            return True
        if d <= self._last_rebalance:
            return False
        if (d - self._last_rebalance).days < REBALANCE_DAYS:
            return False
        return d.weekday() == REBALANCE_WEEKDAY or (d - self._last_rebalance).days >= REBALANCE_DAYS + 3

    def _rebalance(self, d: date, bar_ts) -> list[Event]:
        # events are stamped with bar time, not wall clock, so replay and live
        # runs behave identically (rate limits, daily P&L rolls, journal order)
        momentum = {
            s: self.history[s][-1] / self.history[s][0] - 1.0
            for s in UNIVERSE
        }
        ranked = sorted(momentum, key=momentum.get, reverse=True)
        winners = [s for s in ranked[:TOP_N] if momentum[s] > 0]  # absolute-momentum filter
        # cognition-plane regime signal scales gross exposure; the SignalStore
        # clamps it to [0.3, 1.0], so at worst this de-risks — never levers up
        risk_appetite = self.signals.risk_appetite() if self.signals else 1.0
        weight = (TARGET_GROSS * risk_appetite) / TOP_N if winners else 0.0
        equity = self.portfolio.equity

        events: list[Event] = [
            JournalEntry(
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
        ]

        targets = {s: (weight if s in winners else 0.0) for s in UNIVERSE}
        for symbol, target_w in targets.items():
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
            events.append(
                OrderIntent(
                    strategy=self.name, strategy_version=self.version,
                    symbol=symbol, side=side, qty=qty,
                    reasoning=(
                        f"rebalance to {target_w:.1%} target "
                        f"(momentum {momentum[symbol]:+.1%}, rank {ranked.index(symbol) + 1})"
                    ),
                    ts=bar_ts,
                )
            )
        # sells first so cash frees up before buys hit the gate's cash check
        intents_sorted = sorted(
            events[1:], key=lambda e: 0 if e.side == Side.SELL else 1
        )
        return [events[0], *intents_sorted]
