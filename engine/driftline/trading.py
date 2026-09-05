"""TradingEngine: wires feed → strategies → risk gate → broker → portfolio.

The only component allowed to call a broker, and it only ever does so with a
gate-approved intent. Also emits position/equity snapshots after fills and on
a timer, and keeps the gate's daily-loss tracking fed.

Order path, in order, for every approved intent:
  1. gate.check                      (limits, incl. what is already pending)
  2. gate.reserve                    (cash / exposure / sellable qty held)
  3. OrderUpdate(APPROVED) persisted (durable record BEFORE the broker call;
                                      if the ledger cannot write, the engine
                                      halts and the order is not sent)
  4. broker.submit                   (SUBMITTED / REJECTED, or resolved by
                                      client order id after an ambiguous error)
  5. a terminal OrderUpdate later releases the reservation
"""

from __future__ import annotations

import asyncio
import logging

from .core.bus import CriticalHandlerError, EventBus
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

TERMINAL = {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.VETOED}


class TradingEngine:
    def __init__(self, bus: EventBus, portfolio: Portfolio, gate: RiskGate,
                 broker, strategies: list[Strategy], armed: bool = True,
                 wall_clock_rate_limit: bool = False):
        self.bus = bus
        self.portfolio = portfolio
        self.gate = gate
        self.broker = broker
        self.strategies = strategies
        # replay stamps intents with bar time (all in one wall-clock minute),
        # so the throttle must use bar time there — but live, bar time makes
        # every intent share a minute, so the throttle needs the real clock
        self.wall_clock_rate_limit = wall_clock_rate_limit
        # While not armed (live-mode backfill warm-up), strategies see bars and
        # build state but their OrderIntents are dropped — historical bars must
        # never produce real orders.
        self.armed = armed
        self._halt_announced = False
        bus.subscribe(MarketBar, self.on_bar)
        bus.subscribe(Fill, self.on_fill)
        bus.subscribe(OrderUpdate, self.on_order_update)

    def arm(self, day_start_equity: float | None = None) -> None:
        """End warm-up: allow trading and let strategies reset their cadence.

        The daily-loss baseline is today's earliest known equity (from the
        ledger) when available — falling back to current equity only on the
        first start of a day, so restarts never refresh the loss budget.
        """
        self.armed = True
        self.gate.reset_day(day_start_equity or self.portfolio.equity)
        for s in self.strategies:
            s.on_go_live()
        log.info("engine armed: strategies now trade on fresh bars")

    async def on_bar(self, event: Event) -> None:
        bar: MarketBar = event  # type: ignore[assignment]
        self.portfolio.apply_mark(bar)
        # during warm-up, marks come from historical bars while positions are
        # current — equity is meaningless, so the loss tracker must not watch it
        if self.armed:
            self.gate.observe_equity(self.portfolio.equity, now=bar.ts)
        await self._announce_halt_if_needed()

        for strategy in self.strategies:
            outputs = strategy.on_bar(bar)
            if not self.armed:
                continue  # warm-up: strategies build state, but nothing they
                          # emit (orders OR journal noise) reaches the ledger
            for out in outputs:
                if isinstance(out, OrderIntent):
                    await self._handle_intent(out)
                else:
                    await self.bus.publish(out)

    async def _handle_intent(self, intent: OrderIntent) -> None:
        if not self.armed:
            log.debug("warm-up: dropped %s %s %s", intent.side.value, intent.qty, intent.symbol)
            return
        price = self._price_for(intent.symbol)
        rate_now = None if self.wall_clock_rate_limit else intent.ts
        decision = self.gate.check(intent, price, now=rate_now)
        common = dict(intent_id=intent.intent_id, symbol=intent.symbol, side=intent.side,
                      qty=intent.qty, strategy=intent.strategy,
                      strategy_version=intent.strategy_version, ts=intent.ts)
        if not decision.allowed:
            log.info("VETO %s %s %s: %s", intent.side.value, intent.qty, intent.symbol, decision.reason)
            await self.bus.publish(OrderUpdate(broker_order_id="", status=OrderStatus.VETOED,
                                               reason=decision.reason, **common))
            await self._announce_halt_if_needed()
            return

        # reserve, then make the approval durable before anything crosses the
        # broker boundary — a ledger failure here halts and sends nothing
        self.gate.reserve(intent, price)
        try:
            await self.bus.publish(OrderUpdate(broker_order_id="", status=OrderStatus.APPROVED,
                                               reason=f"gate approved @ {price:.4f}", **common))
        except CriticalHandlerError as exc:
            self.gate.release(intent.intent_id)
            self.gate.halt(f"ledger write failed before order submission: {exc}", kind="infra")
            log.error("ledger failure — halting; %s %s %s NOT sent", intent.side.value, intent.qty, intent.symbol)
            await self._announce_halt_if_needed()
            return

        events = await self.broker.submit(intent, price)
        try:
            await self.bus.publish_many(events)
        except CriticalHandlerError as exc:
            # the order may be live at the broker; the reservation stays until the
            # poller resolves it, but no further orders go out on a broken ledger
            self.gate.halt(f"ledger write failed after order submission: {exc}", kind="infra")
            await self._announce_halt_if_needed()

    async def on_order_update(self, event: Event) -> None:
        upd: OrderUpdate = event  # type: ignore[assignment]
        if upd.status in TERMINAL:
            self.gate.release(upd.intent_id)

    async def on_fill(self, event: Event) -> None:
        fill: Fill = event  # type: ignore[assignment]
        self.gate.consume(fill.intent_id, fill.qty)
        self.portfolio.apply_fill(fill)
        await self.publish_snapshots(ts=fill.ts)

    async def publish_snapshots(self, ts=None) -> None:
        for snap in self.portfolio.position_snapshots(ts=ts):
            await self.bus.publish(snap)
        await self.bus.publish(self.portfolio.equity_snapshot(ts=ts))

    async def snapshot_forever(self, interval_s: int = 60) -> None:
        while True:
            await asyncio.sleep(interval_s)
            self.portfolio.roll_day()
            self.gate.observe_equity(self.portfolio.equity)
            await self._announce_halt_if_needed()
            await self.publish_snapshots()

    async def _announce_halt_if_needed(self) -> None:
        if self.gate.halted and not self._halt_announced:
            self._halt_announced = True
            await self.bus.publish(HaltEvent(source=self.gate.halt_kind or "risk_gate",
                                             reason=self.gate.halt_reason))
        elif not self.gate.halted:
            self._halt_announced = False

    def _price_for(self, symbol: str) -> float:
        for strategy in self.strategies:
            price = getattr(strategy, "last_close", {}).get(symbol)
            if price:
                return price
        pos = self.portfolio.positions.get(symbol)
        return pos.mark if pos else 0.0
