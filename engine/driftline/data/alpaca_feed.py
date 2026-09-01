"""Alpaca market data: historical daily bars (REST) + a daily live poller.

Phase 1 trades at daily cadence, so instead of holding a websocket open all
day we poll for fresh daily bars on a schedule (free IEX-derived data). The
replay feed shares the same MarketBar shape, giving backtest/live parity.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from ..config import Settings
from ..core.bus import EventBus
from ..core.events import MarketBar

log = logging.getLogger(__name__)


class AlpacaFeed:
    def __init__(self, settings: Settings, bus: EventBus, symbols: list[str]):
        self.client = StockHistoricalDataClient(
            settings.alpaca_api_key, settings.alpaca_secret_key
        )
        self.bus = bus
        self.symbols = symbols
        self._last_bar_date: dict[str, str] = {}

    async def backfill(self, days: int = 130) -> None:
        """Publish historical daily bars so strategies warm up their lookbacks."""
        start = datetime.now(timezone.utc) - timedelta(days=int(days * 1.6))
        req = StockBarsRequest(
            symbol_or_symbols=self.symbols, timeframe=TimeFrame.Day, start=start
        )
        bars = await asyncio.to_thread(self.client.get_stock_bars, req)
        events: list[MarketBar] = []
        for symbol in self.symbols:
            for b in bars.data.get(symbol, []):
                events.append(self._to_event(symbol, b))
        events.sort(key=lambda e: e.bar_ts)
        for e in events:
            self._last_bar_date[e.symbol] = e.bar_ts.date().isoformat()
            await self.bus.publish(e)
        log.info("backfilled %d daily bars for %d symbols", len(events), len(self.symbols))

    async def poll_forever(self, interval_s: int = 900) -> None:
        """Publish any newly completed daily bars every `interval_s` seconds."""
        while True:
            try:
                await self._poll_once()
            except Exception:
                log.exception("alpaca poll failed; retrying next cycle")
            await asyncio.sleep(interval_s)

    async def _poll_once(self) -> None:
        start = datetime.now(timezone.utc) - timedelta(days=7)
        req = StockBarsRequest(
            symbol_or_symbols=self.symbols, timeframe=TimeFrame.Day, start=start
        )
        bars = await asyncio.to_thread(self.client.get_stock_bars, req)
        fresh: list[MarketBar] = []
        for symbol in self.symbols:
            for b in bars.data.get(symbol, []):
                e = self._to_event(symbol, b)
                d = e.bar_ts.date().isoformat()
                if self._last_bar_date.get(symbol, "") < d:
                    self._last_bar_date[symbol] = d
                    fresh.append(e)
        fresh.sort(key=lambda e: e.bar_ts)
        for e in fresh:
            await self.bus.publish(e)
        if fresh:
            log.info("published %d fresh daily bars", len(fresh))

    @staticmethod
    def _to_event(symbol: str, b) -> MarketBar:
        return MarketBar(
            symbol=symbol, open=float(b.open), high=float(b.high), low=float(b.low),
            close=float(b.close), volume=float(b.volume),
            bar_ts=b.timestamp if b.timestamp.tzinfo else b.timestamp.replace(tzinfo=timezone.utc),
        )
