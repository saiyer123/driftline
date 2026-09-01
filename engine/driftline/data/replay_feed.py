"""Replay feed: drives the same bus from historical bars (CSV or in-memory).

Used by the end-to-end test and by `runner.py --replay` so the full
strategy → gate → broker → ledger path can run without market hours or keys.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from ..core.bus import EventBus
from ..core.events import MarketBar


def bars_from_csv(path: Path) -> list[MarketBar]:
    """CSV columns: date,symbol,open,high,low,close,volume (date = YYYY-MM-DD)."""
    bars: list[MarketBar] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["date"]).replace(
                hour=21, tzinfo=timezone.utc  # daily bar closes 21:00 UTC (4pm ET)
            )
            bars.append(
                MarketBar(
                    symbol=row["symbol"], open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row["volume"]), bar_ts=ts, ts=ts,
                )
            )
    bars.sort(key=lambda b: (b.bar_ts, b.symbol))
    return bars


class ReplayFeed:
    def __init__(self, bus: EventBus, bars: list[MarketBar]):
        self.bus = bus
        self.bars = sorted(bars, key=lambda b: (b.bar_ts, b.symbol))

    async def run(self) -> None:
        for bar in self.bars:
            await self.bus.publish(bar)
