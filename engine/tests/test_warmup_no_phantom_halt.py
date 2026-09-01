"""Warm-up with seeded live positions must not trip the daily-loss halt.

Regression: on the first VPS start, positions seeded from the broker were
marked against months-old backfill prices, producing phantom equity swings
that halted the engine before it ever armed.
"""

from driftline.config import Settings
from driftline.core.bus import EventBus
from driftline.broker.stub_broker import StubBroker
from driftline.data.replay_feed import ReplayFeed
from driftline.portfolio.accounting import Portfolio, Position
from driftline.risk.gate import RiskGate
from driftline.strategy.baseline_momentum import BaselineMomentum
from driftline.trading import TradingEngine

from tests.test_replay_end_to_end import synthetic_bars


async def test_backfill_with_seeded_positions_does_not_halt(tmp_path):
    settings = Settings(alpaca_paper=True, kill_switch_path=tmp_path / "KILL",
                        db_path=tmp_path / "t.db", event_log_path=tmp_path / "e.jsonl")
    bus = EventBus()
    portfolio = Portfolio(cash=30_000.0)
    # seeded from the broker at today's prices...
    portfolio.positions["SPY"] = Position(symbol="SPY", qty=100, avg_entry=700, mark=700)
    gate = RiskGate(settings, portfolio, kill_switch_path=settings.kill_switch_path)
    engine = TradingEngine(bus, portfolio, gate, StubBroker(),
                           [BaselineMomentum(portfolio)], armed=False)

    # ...then backfill replays bars where SPY trades near $100 — a phantom -85%
    await ReplayFeed(bus, synthetic_bars(140)).run()
    assert not gate.halted  # warm-up marks must not feed the loss tracker

    engine.arm()
    assert not gate.halted
    # after arming, the baseline is the current (distorted-mark) equity, and
    # real observation resumes from there
    assert gate._day_start_equity == portfolio.equity
