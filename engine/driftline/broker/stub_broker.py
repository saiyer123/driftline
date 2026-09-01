"""Stub broker for replay/backtest and tests: fills instantly at last price."""

from __future__ import annotations

from ..core.events import (
    Event,
    Fill,
    OrderIntent,
    OrderStatus,
    OrderUpdate,
    new_id,
)


class StubBroker:
    """Fills every gated order immediately at the provided price.

    `fee_per_share` lets tests exercise fee accounting; Alpaca equities are
    commission-free, but replay results should not assume costlessness.
    """

    def __init__(self, fee_per_share: float = 0.0, slippage_bps: float = 1.0):
        self.fee_per_share = fee_per_share
        self.slippage_bps = slippage_bps

    async def submit(self, intent: OrderIntent, price: float) -> list[Event]:
        broker_order_id = f"stub-{new_id()[:12]}"
        slip = price * self.slippage_bps / 10_000
        fill_price = price + slip if intent.side.value == "buy" else price - slip
        common = dict(
            intent_id=intent.intent_id, broker_order_id=broker_order_id,
            symbol=intent.symbol, side=intent.side, qty=intent.qty,
            strategy=intent.strategy, strategy_version=intent.strategy_version,
        )
        return [
            OrderUpdate(status=OrderStatus.SUBMITTED, ts=intent.ts, **common),
            OrderUpdate(status=OrderStatus.FILLED, ts=intent.ts, **common),
            Fill(price=round(fill_price, 4), fee=self.fee_per_share * intent.qty,
                 ts=intent.ts, **common),
        ]
