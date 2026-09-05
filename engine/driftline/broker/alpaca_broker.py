"""Alpaca paper-trading broker adapter.

Submits gated orders as market DAY orders to the Alpaca paper API. Orders are
tracked until terminal by a background poller — an order submitted overnight
simply stays tracked until it fills at the next open, at which point the Fill
lands on the bus like any other. On startup, open orders from a previous run
are re-adopted (with their strategy attribution restored from the ledger) and
orders that finished while the process was down are ingested from the broker.
Polling (rather than the trade-updates stream) keeps phase 1 simple and is
plenty for a swing cadence; the reconciler backstops anything missed.

Submission failures are classified: a broker rejection is REJECTED; an
ambiguous transport error (timeout, connection reset) is resolved by looking
the order up by our client order id, so a request that was accepted but whose
response was lost is tracked rather than resubmitted.
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


def _is_broker_rejection(exc: Exception) -> bool:
    """A definitive 4xx from Alpaca (bad request, insufficient buying power,
    asset not tradable...) versus an ambiguous transport-level failure."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return isinstance(status, int) and 400 <= status < 500


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


def _side_of(o) -> Side:
    return Side.BUY if str(o.side.value if hasattr(o.side, "value") else o.side) == "buy" else Side.SELL


def _status_of(o) -> str:
    return str(o.status.value if hasattr(o.status, "value") else o.status)


class AlpacaBroker:
    def __init__(self, settings: Settings):
        settings.require_paper()
        self.client = TradingClient(
            settings.alpaca_api_key, settings.alpaca_secret_key, paper=True
        )
        self._open: dict[str, _TrackedOrder] = {}

    # -- submission --------------------------------------------------------------

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
            if _is_broker_rejection(exc):
                log.warning("alpaca rejected %s %s %s: %s",
                            intent.side.value, intent.qty, intent.symbol, exc)
                return [OrderUpdate(broker_order_id="", status=OrderStatus.REJECTED,
                                    reason=str(exc)[:300], **common)]
            # ambiguous: the request may have been accepted. Resolve by client id.
            log.warning("ambiguous submit failure for %s (%s); resolving by client order id",
                        intent.intent_id, exc)
            order = await self._lookup_by_client_id(intent.intent_id)
            if order is None:
                return [OrderUpdate(broker_order_id="", status=OrderStatus.REJECTED,
                                    reason=f"unresolved submit error, not found at broker: {exc}"[:300],
                                    **common)]
        oid = str(order.id)
        self._open[oid] = _TrackedOrder(broker_order_id=oid, **common)
        log.info("submitted %s %s %s (order %s); tracking until terminal",
                 intent.side.value, intent.qty, intent.symbol, oid)
        return [OrderUpdate(broker_order_id=oid, status=OrderStatus.SUBMITTED, **common)]

    async def _lookup_by_client_id(self, client_order_id: str):
        for attempt in range(3):
            try:
                return await asyncio.to_thread(self.client.get_order_by_client_id, client_order_id)
            except Exception as exc:
                if _is_broker_rejection(exc):  # 404: never reached the broker
                    return None
                await asyncio.sleep(1.5 * (attempt + 1))
        return None

    # -- open-order tracking ---------------------------------------------------

    def tracked(self) -> list[_TrackedOrder]:
        return list(self._open.values())

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
                broker_order_id=oid, intent_id=intent_id, symbol=o.symbol,
                side=_side_of(o), qty=float(o.qty or 0),
                strategy=strategy, strategy_version=version,
            )
            log.info("adopted open order %s from a previous run (%s %s %s)",
                     oid, self._open[oid].side.value, self._open[oid].qty, o.symbol)
        return len(self._open)

    async def resolve_missed(self, repo) -> list[Event]:
        """Orders the ledger left non-terminal that finished while we were down.

        Returns the OrderUpdate/Fill events that should be RECORDED (they are
        already reflected in the broker-seeded portfolio, so the caller must
        write them to the ledger without applying them to positions).
        """
        events: list[Event] = []
        for intent in repo.non_terminal_intents():
            if any(t.intent_id == intent["intent_id"] for t in self._open.values()):
                continue  # still open; adopted above
            order = await self._lookup_by_client_id(intent["intent_id"])
            if order is None:
                continue
            status = _status_of(order)
            if status not in _TERMINAL:
                continue
            common = dict(intent_id=intent["intent_id"], symbol=intent["symbol"],
                          side=Side(intent["side"]), qty=intent["qty"],
                          strategy=intent["strategy"], strategy_version=intent["strategy_version"])
            events += self._terminal_events(str(order.id), status, order, common)
            log.info("ingested missed terminal order %s for intent %s (%s)", order.id, intent["intent_id"], status)
        return events

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
        status = _status_of(order)
        if status not in _TERMINAL:
            return []
        del self._open[oid]
        return self._terminal_events(oid, status, order, tracked.common())

    @staticmethod
    def _terminal_events(oid: str, status: str, order, common: dict) -> list[Event]:
        filled_qty = float(order.filled_qty or 0)
        fill_price = float(order.filled_avg_price or 0)
        events: list[Event] = []
        # a canceled/expired DAY order may still have partially filled — those
        # shares traded and the ledger must know, whatever the final status
        if filled_qty > 0 and fill_price > 0:
            events.append(Fill(broker_order_id=oid, price=fill_price, fee=0.0,
                               **{**common, "qty": filled_qty}))
        if status == "filled":
            log.info("order %s filled: %s %s @ %s", oid, common["side"].value, filled_qty, fill_price)
            events.append(OrderUpdate(broker_order_id=oid, status=OrderStatus.FILLED, **common))
        else:
            mapped = OrderStatus.CANCELED if status in ("canceled", "expired") else OrderStatus.REJECTED
            log.info("order %s ended %s (%s/%s filled)", oid, status, filled_qty, common["qty"])
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
