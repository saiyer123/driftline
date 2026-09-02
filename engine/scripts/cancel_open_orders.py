"""Cancel ALL open orders on the Alpaca PAPER account.

    uv run python scripts/cancel_open_orders.py

One-shot cleanup tool (e.g. stale warm-up orders from before the arming fix).
Refuses to run against a live account.
"""

import asyncio
import sys

sys.path.insert(0, ".")

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from driftline.config import settings
from driftline.core.bus import EventBus
from driftline.core.events import OrderStatus, OrderUpdate, Side
from driftline.ledger.repo import LedgerRepo


def main() -> None:
    settings.require_paper()
    client = TradingClient(settings.alpaca_api_key, settings.alpaca_secret_key, paper=True)
    open_orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
    if not open_orders:
        print("no open orders")
        return
    # record the cancellations in the ledger too, so orders don't sit
    # "submitted" forever in the dashboard
    repo = LedgerRepo(settings.db_path)
    bus = EventBus(log_path=settings.event_log_path)
    bus.subscribe("*", repo.on_event)

    async def cancel_all() -> None:
        for o in open_orders:
            print(f"canceling {o.id}: {o.side.value} {o.qty} {o.symbol} ({o.status.value})")
            client.cancel_order_by_id(str(o.id))
            intent_id = str(o.client_order_id or o.id)
            attributed = repo.intent_strategy(intent_id) or ("unknown", "unknown")
            await bus.publish(OrderUpdate(
                intent_id=intent_id, broker_order_id=str(o.id), symbol=o.symbol,
                side=Side.BUY if str(o.side.value) == "buy" else Side.SELL,
                qty=float(o.qty or 0), status=OrderStatus.CANCELED,
                strategy=attributed[0], strategy_version=attributed[1],
                reason="canceled by scripts/cancel_open_orders.py",
            ))

    asyncio.run(cancel_all())
    bus.close()
    print(f"canceled {len(open_orders)} order(s)")


if __name__ == "__main__":
    main()
