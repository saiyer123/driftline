"""Engine entrypoint (shared across every Driftline platform).

  uv run python -m driftline.runner                               # live paper: this platform's default strategies
  uv run python -m driftline.runner --strategies baseline_momentum # any registered set
  uv run python -m driftline.runner --replay bars.csv             # replay a CSV, then serve the API

Both modes run the identical strategy → gate → broker → ledger path; replay
just swaps the feed and the broker. `--strategies` picks names from
strategy/registry.py, which is the only file that differs per platform.

Live start-up sequence (each step is a safety property):
  1. seed positions + cash from the broker                 (state of record)
  2. honor an unresolved halt from the previous run        (persisted halts)
  3. adopt open orders with attribution, reserve them      (gate sees pending)
  4. ingest orders that finished while we were down        (ledger completeness,
     recorded without re-applying: the seed already reflects them)
  5. journal any ledger-vs-broker difference               (external adjustments)
  6. backfill enough history for the longest indicator, unarmed
  7. arm with today's persisted day-start equity           (loss budget survives restarts)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import uvicorn

from .api.server import build_app
from .broker.alpaca_broker import AlpacaBroker
from .broker.stub_broker import StubBroker
from .config import settings
from .core.bus import EventBus
from .core.events import JournalEntry, OrderIntent
from .data.alpaca_feed import AlpacaFeed
from .data.replay_feed import ReplayFeed, bars_from_csv
from .ledger.repo import LedgerRepo
from .portfolio.accounting import Portfolio
from .reconcile import Reconciler
from .risk.gate import RiskGate
from .strategy.registry import (
    DEFAULT_LIVE,
    DEFAULT_REPLAY,
    build_strategies,
    parse_names,
    symbols_for,
)
from .trading import TradingEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("driftline")


def build_common(db_path: Path, cash: float):
    bus = EventBus(log_path=settings.event_log_path)
    repo = LedgerRepo(db_path)
    bus.subscribe("*", repo.on_event, critical=True)  # a ledger that cannot write halts the engine
    portfolio = Portfolio(cash=cash)
    gate = RiskGate(settings, portfolio)
    return bus, repo, portfolio, gate


async def run_replay(csv_path: Path, names: list[str]) -> None:
    db_path = settings.db_path.with_name("driftline-replay.db")
    db_path.unlink(missing_ok=True)
    bus, repo, portfolio, gate = build_common(db_path, cash=100_000.0)
    broker = StubBroker(slippage_bps=1.0)
    engine = TradingEngine(bus, portfolio, gate, broker, build_strategies(names, portfolio))

    bars = bars_from_csv(csv_path)
    await ReplayFeed(bus, bars).run()
    await engine.publish_snapshots()
    log.info("replay done (%s): equity $%.2f (started $100,000.00)", ",".join(names), portfolio.equity)

    app = build_app(bus, repo, portfolio, gate)
    server = uvicorn.Server(uvicorn.Config(app, host=settings.api_host, port=settings.api_port, log_level="warning"))
    log.info("replay API serving on http://%s:%d", settings.api_host, settings.api_port)
    await server.serve()


def _halt_kind_from(halt: dict) -> str:
    if halt.get("source") == "reconcile" or "reconciliation" in halt.get("reason", ""):
        return "reconcile"
    if "daily loss" in halt.get("reason", ""):
        return "daily_loss"
    if halt.get("source") == "infra":
        return "infra"
    return "manual"


async def run_live(names: list[str]) -> None:
    settings.require_paper()
    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        raise SystemExit(
            "Missing Alpaca keys. Copy .env.example to .env at the repo root and add your "
            "ALPACA_API_KEY / ALPACA_SECRET_KEY (paper keys from https://app.alpaca.markets)."
        )

    broker = AlpacaBroker(settings)
    state = await broker.account_state()

    bus, repo, portfolio, gate = build_common(settings.db_path, cash=state["cash"])
    # 1. seed positions from the broker so a restart resumes cleanly
    from .portfolio.accounting import Position
    for symbol, d in state.get("positions_detail", {}).items():
        portfolio.positions[symbol] = Position(
            symbol=symbol, qty=d["qty"], avg_entry=d["avg_entry"], mark=d["mark"]
        )

    from .risk.signal_store import SignalStore
    signals = SignalStore(repo)
    engine = TradingEngine(bus, portfolio, gate, broker,
                           build_strategies(names, portfolio, signals=signals, repo=repo),
                           armed=False,  # warm-up: no orders from historical bars
                           wall_clock_rate_limit=True)
    feed = AlpacaFeed(settings, bus, symbols_for(names))
    reconciler = Reconciler(bus, portfolio, gate, broker)
    app = build_app(bus, repo, portfolio, gate)
    server = uvicorn.Server(uvicorn.Config(app, host=settings.api_host, port=settings.api_port, log_level="warning"))

    log.info("driftline live paper mode [%s]: equity $%.2f, %d position(s); API on http://%s:%d",
             ",".join(names), state["equity"], len(state["positions"]), settings.api_host, settings.api_port)

    # 2. a halt with no later resume survives restarts — systemd's Restart=always
    #    must never clear a daily-loss or reconciliation halt on its own
    open_halt = repo.open_halt()
    if open_halt:
        kind = _halt_kind_from(open_halt)
        gate.halt(f"persisted from previous run [{open_halt['source']}]: {open_halt['reason']}", kind=kind)
        log.warning("starting HALTED [%s] (unresolved halt from previous run): %s", kind, open_halt["reason"])

    # 3. adopt open orders (attribution from the ledger) and reserve them in the gate
    adopted = await broker.adopt_open_orders(repo=repo)
    for t in broker.tracked():
        mark = state.get("positions_detail", {}).get(t.symbol, {}).get("mark", 0.0)
        gate.reserve(OrderIntent(strategy=t.strategy, strategy_version=t.strategy_version,
                                 symbol=t.symbol, side=t.side, qty=t.qty, intent_id=t.intent_id), mark)
    if adopted:
        log.info("tracking %d open order(s) from a previous run (reserved in the gate)", adopted)

    # 4. orders that finished while we were down: record them (the broker seed
    #    already reflects their effect, so they are NOT applied to the portfolio)
    for e in await broker.resolve_missed(repo):
        repo.record(e)

    # 5. explain the ledger-vs-broker gap (manual trades, missed events) in the journal
    ledger_pos = repo.ledger_positions()
    diffs = []
    for sym in sorted(set(ledger_pos) | set(state["positions"])):
        a, b = ledger_pos.get(sym, 0.0), state["positions"].get(sym, 0.0)
        if abs(a - b) > 1e-4:
            diffs.append(f"{sym}: ledger {a:g} -> broker {b:g}")
    if diffs:
        await bus.publish(JournalEntry(
            strategy="engine", strategy_version="restart", kind="adjustment",
            text="restart re-synced from broker; ledger fills do not explain: " + "; ".join(diffs),
            payload={"ledger": ledger_pos, "broker": state["positions"], "cash": state["cash"]},
        ))
        log.warning("external adjustment recorded: %s", "; ".join(diffs))

    # 6. backfill enough completed sessions for the longest indicator any strategy uses
    needed = max([130, *(getattr(s, "history_needed", 0) + 20 for s in engine.strategies)])
    await feed.backfill(days=needed)
    # 7. arm with today's persisted baseline so a restart never refreshes the loss budget
    engine.arm(day_start_equity=repo.day_start_equity(datetime.now(timezone.utc).date().isoformat()))
    await engine.publish_snapshots()
    await asyncio.gather(
        feed.poll_forever(),
        broker.poll_open_orders_forever(bus),
        engine.snapshot_forever(),
        reconciler.run_forever(),
        server.serve(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Driftline trading engine (paper only)")
    parser.add_argument("--replay", type=Path, help="replay a bars CSV instead of live paper trading")
    parser.add_argument("--strategies", default=None,
                        help=f"comma-separated strategy names (live default: {DEFAULT_LIVE}; "
                             f"replay default: {DEFAULT_REPLAY})")
    args = parser.parse_args()
    if args.replay:
        asyncio.run(run_replay(args.replay, parse_names(args.strategies or DEFAULT_REPLAY)))
    else:
        asyncio.run(run_live(parse_names(args.strategies or DEFAULT_LIVE)))


if __name__ == "__main__":
    main()
