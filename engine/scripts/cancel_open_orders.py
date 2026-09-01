"""Cancel ALL open orders on the Alpaca PAPER account.

    uv run python scripts/cancel_open_orders.py

One-shot cleanup tool (e.g. stale warm-up orders from before the arming fix).
Refuses to run against a live account.
"""

import sys

sys.path.insert(0, ".")

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from driftline.config import settings


def main() -> None:
    settings.require_paper()
    client = TradingClient(settings.alpaca_api_key, settings.alpaca_secret_key, paper=True)
    open_orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
    if not open_orders:
        print("no open orders")
        return
    for o in open_orders:
        print(f"canceling {o.id}: {o.side.value} {o.qty} {o.symbol} ({o.status.value})")
        client.cancel_order_by_id(str(o.id))
    print(f"canceled {len(open_orders)} order(s)")


if __name__ == "__main__":
    main()
