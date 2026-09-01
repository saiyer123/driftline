"""Engine entrypoint.

  uv run python -m driftline.runner            # live paper mode (needs .env keys)
  uv run python -m driftline.runner --replay bars.csv   # replay a CSV, then serve the API

Both modes run the identical strategy → gate → broker → ledger path; replay
just swaps the feed and the broker.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import uvicorn

from .api.server import build_app
from .broker.alpaca_broker import AlpacaBroker
from .broker.stub_broker import StubBroker
from .config import settings
from .core.bus import EventBus
from .data.alpaca_feed import AlpacaFeed
from .data.replay_feed import ReplayFeed, bars_from_csv
from .ledger.repo import LedgerRepo
from .portfolio.accounting import Portfolio
from .reconcile import Reconciler
from .risk.gate import RiskGate
from .strategy.baseline_momentum import UNIVERSE, BaselineMomentum
from .trading import TradingEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("driftline")


def build_common(db_path: Path, cash: float):
    bus = EventBus(log_path=settings.event_log_path)
    repo = LedgerRepo(db_path)
    bus.subscribe("*", repo.on_event)
    portfolio = Portfolio(cash=cash)
    gate = RiskGate(settings, portfolio)
    return bus, repo, portfolio, gate


async def run_replay(csv_path: Path) -> None:
    db_path = settings.db_path.with_name("driftline-replay.db")
    db_path.unlink(missing_ok=True)
    bus, repo, portfolio, gate = build_common(db_path, cash=100_000.0)
    broker = StubBroker(slippage_bps=1.0)
    engine = TradingEngine(bus, portfolio, gate, broker, [BaselineMomentum(portfolio)])

    bars = bars_from_csv(csv_path)
    await ReplayFeed(bus, bars).run()
    await engine.publish_snapshots()
    log.info("replay done: equity $%.2f (started $100,000.00)", portfolio.equity)

    app = build_app(bus, repo, portfolio, gate)
    server = uvicorn.Server(uvicorn.Config(app, host=settings.api_host, port=settings.api_port, log_level="warning"))
    log.info("replay API serving on http://%s:%d", settings.api_host, settings.api_port)
    await server.serve()


async def run_live() -> None:
    settings.require_paper()
    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        raise SystemExit(
            "Missing Alpaca keys. Copy .env.example to .env at the repo root and add your "
            "ALPACA_API_KEY / ALPACA_SECRET_KEY (paper keys from https://app.alpaca.markets)."
        )

    broker = AlpacaBroker(settings)
    state = await broker.account_state()

    bus, repo, portfolio, gate = build_common(settings.db_path, cash=state["cash"])
    # seed positions from the broker so a restart resumes cleanly
    from .portfolio.accounting import Position
    for symbol, d in state.get("positions_detail", {}).items():
        portfolio.positions[symbol] = Position(
            symbol=symbol, qty=d["qty"], avg_entry=d["avg_entry"], mark=d["mark"]
        )

    engine = TradingEngine(bus, portfolio, gate, broker, [BaselineMomentum(portfolio)])
    feed = AlpacaFeed(settings, bus, UNIVERSE)
    reconciler = Reconciler(bus, portfolio, gate, broker)
    app = build_app(bus, repo, portfolio, gate)
    server = uvicorn.Server(uvicorn.Config(app, host=settings.api_host, port=settings.api_port, log_level="warning"))

    log.info("driftline live paper mode: equity $%.2f, %d position(s); API on http://%s:%d",
             state["equity"], len(state["positions"]), settings.api_host, settings.api_port)

    await feed.backfill()
    await engine.publish_snapshots()
    await asyncio.gather(
        feed.poll_forever(),
        engine.snapshot_forever(),
        reconciler.run_forever(),
        server.serve(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Driftline trading engine (paper only)")
    parser.add_argument("--replay", type=Path, help="replay a bars CSV instead of live paper trading")
    args = parser.parse_args()
    if args.replay:
        asyncio.run(run_replay(args.replay))
    else:
        asyncio.run(run_live())


if __name__ == "__main__":
    main()
