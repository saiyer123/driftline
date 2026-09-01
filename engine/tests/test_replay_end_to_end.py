"""End-to-end: synthetic daily bars → strategy → gate → stub broker → ledger.

Asserts the full pipeline produces consistent state: fills recorded, positions
long-only within limits, ledger totals matching portfolio state.
"""

import math
import random
from datetime import datetime, timedelta, timezone

from driftline.config import Settings
from driftline.core.bus import EventBus
from driftline.core.events import MarketBar
from driftline.broker.stub_broker import StubBroker
from driftline.data.replay_feed import ReplayFeed
from driftline.ledger.repo import LedgerRepo
from driftline.portfolio.accounting import Portfolio
from driftline.risk.gate import RiskGate
from driftline.strategy.baseline_momentum import TOP_N, UNIVERSE, BaselineMomentum
from driftline.trading import TradingEngine

START = datetime(2026, 1, 5, 21, 0, tzinfo=timezone.utc)


def synthetic_bars(days: int = 140) -> list[MarketBar]:
    rng = random.Random(42)
    drifts = {s: rng.uniform(-0.0005, 0.0015) for s in UNIVERSE}
    prices = {s: 100.0 * (1 + i) for i, s in enumerate(UNIVERSE)}
    bars: list[MarketBar] = []
    ts = START
    for _ in range(days):
        if ts.weekday() < 5:  # trading days only
            for s in UNIVERSE:
                ret = drifts[s] + rng.gauss(0, 0.01)
                prices[s] *= math.exp(ret)
                close = round(prices[s], 2)
                bars.append(MarketBar(
                    symbol=s, open=close, high=close * 1.005, low=close * 0.995,
                    close=close, volume=1_000_000, bar_ts=ts, ts=ts,
                ))
        ts += timedelta(days=1)
    return bars


async def test_replay_pipeline(tmp_path):
    settings = Settings(
        alpaca_paper=True,
        db_path=tmp_path / "test.db",
        event_log_path=tmp_path / "events.jsonl",
        kill_switch_path=tmp_path / "KILL",
    )
    bus = EventBus(log_path=settings.event_log_path)
    repo = LedgerRepo(settings.db_path)
    bus.subscribe("*", repo.on_event)

    portfolio = Portfolio(cash=100_000.0)
    gate = RiskGate(settings, portfolio, kill_switch_path=settings.kill_switch_path)
    engine = TradingEngine(bus, portfolio, gate, StubBroker(), [BaselineMomentum(portfolio)])

    await ReplayFeed(bus, synthetic_bars()).run()
    await engine.publish_snapshots()
    bus.close()

    fills = repo.fills(limit=10_000)
    orders = repo.orders(limit=10_000)
    journal = repo.journal(limit=10_000)
    curve = repo.equity_curve(since_hours=24 * 365 * 10)

    # The pipeline actually traded and journaled its reasoning
    assert len(fills) > 0
    assert len(journal) > 0
    assert any(o["status"] == "filled" for o in orders)

    # Long-only invariant held everywhere
    for p in portfolio.positions.values():
        assert p.qty >= 0

    # Position sizes respected the gate (with slack for post-fill price drift)
    equity = portfolio.equity
    for p in portfolio.positions.values():
        assert p.market_value <= settings.max_position_pct * equity * 1.3

    # At most TOP_N holdings at the end
    open_positions = [p for p in portfolio.positions.values() if p.qty > 0]
    assert len(open_positions) <= TOP_N

    # Ledger fills replayed through fresh accounting reproduce engine cash exactly
    from driftline.core.events import Fill, Side
    replayed = Portfolio(cash=100_000.0)
    for f in reversed(fills):  # repo returns newest first
        replayed.apply_fill(Fill(
            intent_id=f["intent_id"], broker_order_id="x", symbol=f["symbol"],
            side=Side(f["side"]), qty=f["qty"], price=f["price"], fee=f["fee"],
            strategy=f["strategy"], strategy_version=f["strategy_version"],
        ))
    assert abs(replayed.cash - portfolio.cash) < 0.01

    # Equity snapshots were recorded and end near the final portfolio equity
    assert len(curve) > 0
    assert abs(curve[-1]["equity"] - portfolio.equity) < 1.0


async def test_kill_switch_stops_trading(tmp_path):
    settings = Settings(
        alpaca_paper=True,
        db_path=tmp_path / "test.db",
        event_log_path=tmp_path / "events.jsonl",
        kill_switch_path=tmp_path / "KILL",
    )
    bus = EventBus()
    portfolio = Portfolio(cash=100_000.0)
    gate = RiskGate(settings, portfolio, kill_switch_path=settings.kill_switch_path)
    engine = TradingEngine(bus, portfolio, gate, StubBroker(), [BaselineMomentum(portfolio)])

    gate.kill("test")
    await ReplayFeed(bus, synthetic_bars()).run()
    assert not portfolio.positions  # nothing ever traded
    assert portfolio.cash == 100_000.0
