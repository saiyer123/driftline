"""Alpaca paper-trading broker adapter.

Submits gated orders as market DAY orders to the Alpaca paper API. Orders are
tracked until terminal by a background poller — an order submitted overnight
simply stays tracked until it fills at the next open, at which point the Fill
lands on the bus like any other. On startup, open orders from a previous run
are re-adopted so restarts don't orphan them. Polling (rather than the
trade-updates stream) keeps phase 1 simple and is plenty for a swing cadence;
the reconciler backstops anything missed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

from ..config import Settings
from ..core.events import (
    Event,
    Fill,
    OrderIntent,
    OrderStatus,
    OrderUpdate,
    Side,
)

log = logging.getLogger(__name__)

_TERMINAL = {"filled", "canceled", "expired", "rejected"}


@dataclass
class _TrackedOrder:
    broker_order_id: str
    intent_id: str
    symbol: str
    side: Side
    qty: float
    strategy: str
    strategy_version: str

    def common(self) -> dict:
        return dict(
            intent_id=self.intent_id, symbol=self.symbol, side=self.side,
            qty=self.qty, strategy=self.strategy,
            strategy_version=self.strategy_version,
        )


class AlpacaBroker:
    def __init__(self, settings: Settings):
        settings.require_paper()
        self.client = TradingClient(
            settings.alpaca_api_key, settings.alpaca_secret_key, paper=True
        )
        self._open: dict[str, _TrackedOrder] = {}

    async def submit(self, intent: OrderIntent, price: float) -> list[Event]:
        common = dict(
            intent_id=intent.intent_id, symbol=intent.symbol, side=intent.side,
            qty=intent.qty, strategy=intent.strategy,
            strategy_version=intent.strategy_version,
        )
        req = MarketOrderRequest(
            symbol=intent.symbol,
            qty=intent.qty,
            side=OrderSide.BUY if intent.side == Side.BUY else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=intent.intent_id,
        )
        try:
            order = await asyncio.to_thread(self.client.submit_order, req)
        except Exception as exc:
            log.warning("alpaca rejected %s %s %s: %s",
                        intent.side.value, intent.qty, intent.symbol, exc)
            return [OrderUpdate(broker_order_id="", status=OrderStatus.REJECTED,
                                reason=str(exc), **common)]

        oid = str(order.id)
        self._open[oid] = _TrackedOrder(broker_order_id=oid, **common)
        log.info("submitted %s %s %s (order %s); tracking until terminal",
                 intent.side.value, intent.qty, intent.symbol, oid)
        return [OrderUpdate(broker_order_id=oid, status=OrderStatus.SUBMITTED, **common)]

    # -- open-order tracking ---------------------------------------------------

    async def adopt_open_orders(self, repo=None) -> int:
        """Re-adopt open orders left by a previous run (e.g. after a restart).

        client_order_id is our intent_id, so the ledger can restore each
        order's strategy attribution instead of tagging fills "unknown".
        """
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        orders = await asyncio.to_thread(self.client.get_orders, req)
        for o in orders:
            oid = str(o.id)
            if oid in self._open:
                continue
            intent_id = str(o.client_order_id or oid)
            strategy, version = ("unknown", "unknown")
            if repo is not None:
                attributed = repo.intent_strategy(intent_id)
                if attributed:
                    strategy, version = attributed
            self._open[oid] = _TrackedOrder(
                broker_order_id=oid,
                intent_id=intent_id,
                symbol=o.symbol,
                side=Side.BUY if str(o.side.value if hasattr(o.side, "value") else o.side) == "buy" else Side.SELL,
                qty=float(o.qty or 0),
                strategy=strategy, strategy_version=version,
            )
            log.info("adopted open order %s from a previous run (%s %s %s)",
                     oid, self._open[oid].side.value, self._open[oid].qty, o.symbol)
        return len(self._open)

    async def poll_open_orders_forever(self, bus, interval_s: int = 30) -> None:
        """Poll tracked orders; publish updates/fills when they reach terminal state."""
        while True:
            await asyncio.sleep(interval_s)
            if not self._open:
                continue
            for oid in list(self._open):
                try:
                    events = await self._check_order(oid)
                except Exception:
                    log.exception("polling order %s failed; will retry", oid)
                    continue
                for e in events:
                    await bus.publish(e)

    async def _check_order(self, oid: str) -> list[Event]:
        tracked = self._open[oid]
        order = await asyncio.to_thread(self.client.get_order_by_id, oid)
        status = str(order.status.value if hasattr(order.status, "value") else order.status)
        if status not in _TERMINAL:
            return []
        del self._open[oid]
        common = tracked.common()
        filled_qty = float(order.filled_qty or 0)
        fill_price = float(order.filled_avg_price or 0)
        events: list[Event] = []
        # a canceled/expired DAY order may still have partially filled — those
        # shares traded and the ledger must know, whatever the final status
        if filled_qty > 0 and fill_price > 0:
            events.append(Fill(broker_order_id=oid, price=fill_price, fee=0.0,
                               **{**common, "qty": filled_qty}))
        if status == "filled":
            log.info("order %s filled: %s %s @ %s", oid, tracked.side.value, filled_qty, fill_price)
            events.append(OrderUpdate(broker_order_id=oid, status=OrderStatus.FILLED, **common))
        else:
            mapped = OrderStatus.CANCELED if status in ("canceled", "expired") else OrderStatus.REJECTED
            log.info("order %s ended %s (%s/%s filled)", oid, status, filled_qty, tracked.qty)
            events.append(OrderUpdate(broker_order_id=oid, status=mapped,
                                      reason=f"alpaca status: {status}", **common))
        return events

    # -- reconciliation reads --------------------------------------------------

    @property
    def has_open_orders(self) -> bool:
        return bool(self._open)

    async def account_state(self) -> dict:
        acct = await asyncio.to_thread(self.client.get_account)
        positions = await asyncio.to_thread(self.client.get_all_positions)
        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "positions": {p.symbol: float(p.qty) for p in positions},
            "positions_detail": {
                p.symbol: {
                    "qty": float(p.qty),
                    "avg_entry": float(p.avg_entry_price),
                    "mark": float(p.current_price or p.avg_entry_price),
                }
                for p in positions
            },
        }
