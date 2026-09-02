"""Earnings-drift strategy: trade the post-earnings announcement drift.

Deterministic rules consuming the analyst's bounded earnings signals:

  ENTRY: a watchlist symbol has a fresh earnings signal (analyst score >=
  ENTRY_SCORE, confidence >= ENTRY_CONFIDENCE, within the freshness window)
  and we hold no position in it -> buy a fixed small weight. Long-only:
  negative scores are never shorted, only avoided.

  EXIT: after HOLD_DAYS trading days (the drift window per the PEAD
  literature), sell the position. Exits are idempotent: if a sell is vetoed
  (halt, rate limit), it re-emits on the next bar until flat.

The portfolio is the source of truth for what is actually held — the
strategy only tracks entry dates, rebuilding them from ledger fills on
restart, and drops phantom entries whose buy never filled.
"""

from __future__ import annotations

from datetime import date, datetime

from ..core.events import Event, JournalEntry, MarketBar, OrderIntent, Side
from .base import Strategy
from .watchlist import WATCHLIST

ENTRY_SCORE = 0.5
ENTRY_CONFIDENCE = 0.5
POSITION_WEIGHT = 0.05   # 5% of equity per name
MAX_CONCURRENT = 4
HOLD_DAYS = 12           # trading days in the drift window
MIN_TRADE_VALUE = 100


class EarningsDrift(Strategy):
    name = "earnings_drift"

    def __init__(self, portfolio, signals, repo=None):
        super().__init__(portfolio)
        self.signals = signals
        self.last_close: dict[str, float] = {}
        self.entries: dict[str, dict] = {}  # symbol -> {"entered": date, "days_held": int}
        self._day_counted: dict[str, date] = {}
        if repo is not None:
            for sym, p in repo.strategy_positions(self.name).items():
                entered = datetime.fromisoformat(p["last_entry"]).date() if p["last_entry"] else None
                # seed the hold clock from stored bars so a deploy mid-hold
                # doesn't restart the 12-session window from zero
                days_held = 0
                if entered is not None:
                    days_held = sum(
                        1 for b in repo.daily_bars(sym, limit=HOLD_DAYS * 2)
                        if b["date"] > entered.isoformat()
                    )
                self.entries[sym] = {"entered": entered, "days_held": days_held}

    def on_go_live(self) -> None:
        pass  # no cadence state; entries depend only on fresh signals

    def _held_qty(self, symbol: str) -> float:
        pos = self.portfolio.positions.get(symbol)
        return pos.qty if pos else 0.0

    def on_bar(self, bar: MarketBar) -> list[Event]:
        if bar.symbol not in WATCHLIST:
            return []
        self.last_close[bar.symbol] = bar.close
        bar_date = bar.bar_ts.date()
        symbol = bar.symbol
        qty_held = self._held_qty(symbol)
        entry = self.entries.get(symbol)

        if entry:
            if qty_held <= 0:
                # buy never filled (vetoed/rejected), or exit completed — clear state
                if entry.get("exiting") or (entry["entered"] and bar_date > entry["entered"]):
                    del self.entries[symbol]
                return []
            if self._day_counted.get(symbol) != bar_date:
                self._day_counted[symbol] = bar_date
                if entry["entered"] and bar_date > entry["entered"]:
                    entry["days_held"] += 1
            if entry["days_held"] >= HOLD_DAYS:
                events: list[Event] = []
                if not entry.get("exiting"):
                    entry["exiting"] = True
                    events.append(JournalEntry(
                        strategy=self.name, strategy_version=self.version, kind="decision",
                        text=f"{bar_date} exit {symbol}: drift window complete ({HOLD_DAYS} sessions held)",
                        ts=bar.bar_ts,
                    ))
                # idempotent: re-emit until the position is actually flat
                events.append(OrderIntent(
                    strategy=self.name, strategy_version=self.version,
                    symbol=symbol, side=Side.SELL, qty=qty_held,
                    reasoning=f"drift window complete after {HOLD_DAYS} sessions",
                    ts=bar.bar_ts,
                ))
                return events
            return []

        # entry path — no tracked entry for this symbol
        if qty_held > 0:
            return []  # position exists that we didn't enter this run; leave it alone
        if len(self.entries) >= MAX_CONCURRENT or self.signals is None:
            return []
        event = self.signals.earnings_event(symbol)
        if not event or event["score"] < ENTRY_SCORE or event["confidence"] < ENTRY_CONFIDENCE:
            return []
        if bar.close <= 0:
            return []
        target_value = POSITION_WEIGHT * self.portfolio.equity
        if target_value < MIN_TRADE_VALUE:
            return []
        qty = round(target_value / bar.close, 4)
        self.entries[symbol] = {"entered": bar_date, "days_held": 0}
        self._day_counted[symbol] = bar_date
        return [
            JournalEntry(
                strategy=self.name, strategy_version=self.version, kind="decision",
                text=(f"{bar_date} enter {symbol} at {POSITION_WEIGHT:.0%} of equity: "
                      f"earnings score {event['score']:+.2f} (conf {event['confidence']:.2f}), "
                      f"holding {HOLD_DAYS} sessions"),
                ts=bar.bar_ts,
            ),
            OrderIntent(
                strategy=self.name, strategy_version=self.version,
                symbol=symbol, side=Side.BUY, qty=qty,
                reasoning=f"post-earnings drift entry, score {event['score']:+.2f}",
                ts=bar.bar_ts,
            ),
        ]
