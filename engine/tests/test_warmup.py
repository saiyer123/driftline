"""Warm-up safety: historical backfill bars must never produce orders."""

from driftline.config import Settings
from driftline.core.bus import EventBus
from driftline.broker.stub_broker import StubBroker
from driftline.data.replay_feed import ReplayFeed
from driftline.portfolio.accounting import Portfolio
from driftline.risk.gate import RiskGate
from driftline.strategy.baseline_momentum import BaselineMomentum
from driftline.trading import TradingEngine

from tests.test_replay_end_to_end import synthetic_bars


def make_engine(tmp_path, armed):
    settings = Settings(alpaca_paper=True, kill_switch_path=tmp_path / "KILL",
                        db_path=tmp_path / "t.db", event_log_path=tmp_path / "e.jsonl")
    bus = EventBus()
    portfolio = Portfolio(cash=100_000.0)
    gate = RiskGate(settings, portfolio, kill_switch_path=settings.kill_switch_path)
    strategy = BaselineMomentum(portfolio)
    engine = TradingEngine(bus, portfolio, gate, StubBroker(), [strategy], armed=armed)
    return bus, portfolio, engine, strategy


async def test_unarmed_engine_never_trades(tmp_path):
    bus, portfolio, engine, _ = make_engine(tmp_path, armed=False)
    await ReplayFeed(bus, synthetic_bars()).run()
    assert not portfolio.positions
    assert portfolio.cash == 100_000.0


async def test_arm_after_warmup_trades_on_next_bar(tmp_path):
    bus, portfolio, engine, strategy = make_engine(tmp_path, armed=False)
    bars = synthetic_bars(160)
    warmup, live = bars[:-16], bars[-16:]  # last two trading days as "fresh"

    await ReplayFeed(bus, warmup).run()
    assert not portfolio.positions  # warm-up produced no trades

    engine.arm()
    assert strategy._last_rebalance is None  # cadence reset by on_go_live
    await ReplayFeed(bus, live).run()
    assert any(p.qty > 0 for p in portfolio.positions.values())  # traded on fresh bars
