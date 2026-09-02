"""Rotation under realistic (delayed) fills — the review's key missing test.

Live Alpaca DAY orders fill at the next open, not inside submit(). A rotation
that assumes instant fills gets its buys vetoed (cash still tied up in the
not-yet-filled sells) and, before the target-seeking fix, never retried them.
"""

from driftline.config import Settings
from driftline.core.bus import EventBus
from driftline.core.events import Event, Fill, OrderIntent, OrderStatus, OrderUpdate, new_id
from driftline.data.replay_feed import ReplayFeed
from driftline.portfolio.accounting import Portfolio
from driftline.risk.gate import RiskGate
from driftline.strategy.baseline_momentum import BaselineMomentum
from driftline.trading import TradingEngine

from tests.test_replay_end_to_end import synthetic_bars


class DelayedStubBroker:
    """Accepts orders but only fills them when `flush()` is called — the test
    calls flush between days, mimicking next-open fills."""

    def __init__(self):
        self.pending: list[tuple[OrderIntent, float]] = []

    async def submit(self, intent: OrderIntent, price: float) -> list[Event]:
        self.pending.append((intent, price))
        return [OrderUpdate(
            intent_id=intent.intent_id, broker_order_id=f"d-{new_id()[:8]}",
            symbol=intent.symbol, side=intent.side, qty=intent.qty,
            status=OrderStatus.SUBMITTED, strategy=intent.strategy,
            strategy_version=intent.strategy_version, ts=intent.ts,
        )]

    def flush(self) -> list[Fill]:
        fills = [
            Fill(intent_id=i.intent_id, broker_order_id="d", symbol=i.symbol,
                 side=i.side, qty=i.qty, price=price, fee=0.0,
                 strategy=i.strategy, strategy_version=i.strategy_version, ts=i.ts)
            for i, price in self.pending
        ]
        self.pending = []
        return fills


async def test_rotation_completes_under_delayed_fills(tmp_path):
    settings = Settings(alpaca_paper=True, kill_switch_path=tmp_path / "KILL",
                        db_path=tmp_path / "t.db", event_log_path=tmp_path / "e.jsonl")
    bus = EventBus()
    portfolio = Portfolio(cash=100_000.0)
    gate = RiskGate(settings, portfolio, kill_switch_path=settings.kill_switch_path)
    broker = DelayedStubBroker()
    strategy = BaselineMomentum(portfolio)  # no signals -> appetite 1.0, the worst case
    engine = TradingEngine(bus, portfolio, gate, broker, [strategy])

    # group synthetic bars by day and interleave: bars -> flush fills (next open)
    bars = synthetic_bars(240)
    by_day: dict = {}
    for b in bars:
        by_day.setdefault(b.bar_ts.date(), []).append(b)

    for day in sorted(by_day):
        for fill in broker.flush():  # yesterday's orders fill at today's open
            await bus.publish(fill)
        await ReplayFeed(bus, by_day[day]).run()

    for fill in broker.flush():
        await bus.publish(fill)

    # after many weekly rotations with day-delayed fills, the book must be
    # invested near its targets, not stuck under-invested from vetoed buys
    equity = portfolio.equity
    gross = portfolio.gross_exposure
    assert gross > 0.5 * equity, f"under-invested: gross {gross:.0f} vs equity {equity:.0f}"
    for p in portfolio.positions.values():
        assert p.qty >= 0  # long-only held throughout
