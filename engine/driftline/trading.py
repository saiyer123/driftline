"""TradingEngine: wires feed → strategies → risk gate → broker → portfolio.

The only component allowed to call a broker, and it only ever does so with a
gate-approved intent. Also emits position/equity snapshots after fills and on
a timer, and keeps the gate's daily-loss tracking fed.
"""

from __future__ import annotations

import asyncio
import logging

from .core.bus import EventBus
from .core.events import (
    Event,
    Fill,
    HaltEvent,
    MarketBar,
    OrderIntent,
    OrderStatus,
    OrderUpdate,
)
from .portfolio.accounting import Portfolio
from .risk.gate import RiskGate
from .strategy.base import Strategy

log = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self, bus: EventBus, portfolio: Portfolio, gate: RiskGate,
                 broker, strategies: list[Strategy]):
        self.bus = bus
        self.portfolio = portfolio
        self.gate = gate
        self.broker = broker
        self.strategies = strategies
        self._halt_announced = False
        bus.subscribe(MarketBar, self.on_bar)
        bus.subscribe(Fill, self.on_fill)

    async def on_bar(self, event: Event) -> None:
        bar: MarketBar = event  # type: ignore[assignment]
        self.portfolio.apply_mark(bar)
        self.gate.observe_equity(self.portfolio.equity, now=bar.ts)
        await self._announce_halt_if_needed()

        for strategy in self.strategies:
            outputs = strategy.on_bar(bar)
            for out in outputs:
                if isinstance(out, OrderIntent):
                    await self._handle_intent(out)
                else:
                    await self.bus.publish(out)

    async def _handle_intent(self, intent: OrderIntent) -> None:
        price = self._price_for(intent.symbol)
        decision = self.gate.check(intent, price, now=intent.ts)
        if not decision.allowed:
            log.info("VETO %s %s %s: %s", intent.side.value, intent.qty, intent.symbol, decision.reason)
            await self.bus.publish(OrderUpdate(
                intent_id=intent.intent_id, broker_order_id="", symbol=intent.symbol,
                side=intent.side, qty=intent.qty, status=OrderStatus.VETOED,
                strategy=intent.strategy, strategy_version=intent.strategy_version,
                reason=decision.reason, ts=intent.ts,
            ))
            await self._announce_halt_if_needed()
            return
        events = await self.broker.submit(intent, price)
        await self.bus.publish_many(events)

    async def on_fill(self, event: Event) -> None:
        fill: Fill = event  # type: ignore[assignment]
        self.portfolio.apply_fill(fill)
        await self.publish_snapshots(ts=fill.ts)

    async def publish_snapshots(self, ts=None) -> None:
        for snap in self.portfolio.position_snapshots(ts=ts):
            await self.bus.publish(snap)
        await self.bus.publish(self.portfolio.equity_snapshot(ts=ts))

    async def snapshot_forever(self, interval_s: int = 60) -> None:
        while True:
            await asyncio.sleep(interval_s)
            self.gate.observe_equity(self.portfolio.equity)
            await self._announce_halt_if_needed()
            await self.publish_snapshots()

    async def _announce_halt_if_needed(self) -> None:
        if self.gate.halted and not self._halt_announced:
            self._halt_announced = True
            await self.bus.publish(HaltEvent(source="risk_gate", reason=self.gate.halt_reason))
        elif not self.gate.halted:
            self._halt_announced = False

    def _price_for(self, symbol: str) -> float:
        for strategy in self.strategies:
            price = getattr(strategy, "last_close", {}).get(symbol)
            if price:
                return price
        pos = self.portfolio.positions.get(symbol)
        return pos.mark if pos else 0.0
