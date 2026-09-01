"""Alpaca paper-trading broker adapter.

Submits gated orders as market DAY orders to the Alpaca paper API and polls
order status until terminal, mapping updates and fills back to bus events.
Polling (rather than the trade-updates stream) keeps phase 1 simple and is
plenty for a swing cadence; the reconciler backstops anything missed.
"""

from __future__ import annotations

import asyncio
import logging

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

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


class AlpacaBroker:
    def __init__(self, settings: Settings):
        settings.require_paper()
        self.client = TradingClient(
            settings.alpaca_api_key, settings.alpaca_secret_key, paper=True
        )

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
            log.warning("alpaca rejected %s %s %s: %s", intent.side.value, intent.qty, intent.symbol, exc)
            return [OrderUpdate(broker_order_id="", status=OrderStatus.REJECTED,
                                reason=str(exc), **common)]

        events: list[Event] = [
            OrderUpdate(broker_order_id=str(order.id), status=OrderStatus.SUBMITTED, **common)
        ]
        events += await self._await_terminal(str(order.id), common)
        return events

    async def _await_terminal(self, order_id: str, common: dict, timeout_s: float = 120) -> list[Event]:
        elapsed = 0.0
        interval = 2.0
        while elapsed < timeout_s:
            await asyncio.sleep(interval)
            elapsed += interval
            order = await asyncio.to_thread(self.client.get_order_by_id, order_id)
            status = str(order.status.value if hasattr(order.status, "value") else order.status)
            if status in _TERMINAL:
                events: list[Event] = []
                if status == "filled":
                    filled_qty = float(order.filled_qty or 0)
                    fill_price = float(order.filled_avg_price or 0)
                    events.append(OrderUpdate(broker_order_id=order_id,
                                              status=OrderStatus.FILLED, **common))
                    events.append(Fill(broker_order_id=order_id, price=fill_price,
                                       fee=0.0, **{**common, "qty": filled_qty}))
                else:
                    mapped = OrderStatus.CANCELED if status in ("canceled", "expired") else OrderStatus.REJECTED
                    events.append(OrderUpdate(broker_order_id=order_id, status=mapped,
                                              reason=f"alpaca status: {status}", **common))
                return events
        log.warning("order %s not terminal after %ss; reconciler will pick it up", order_id, timeout_s)
        return []

    # -- reconciliation reads -------------------------------------------------

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
