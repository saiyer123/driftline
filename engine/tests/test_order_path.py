"""Engine order path: durable approval before the broker, ledger failure halts,
reservations released on terminal updates, adjustment/missed-fill plumbing."""

from datetime import datetime, timezone

from driftline.broker.stub_broker import StubBroker
from driftline.config import Settings
from driftline.core.bus import EventBus
from driftline.core.events import MarketBar, OrderIntent, OrderStatus, Side
from driftline.ledger.repo import LedgerRepo
from driftline.portfolio.accounting import Portfolio
from driftline.risk.gate import RiskGate
from driftline.strategy.base import Strategy
from driftline.trading import TradingEngine

TS = datetime(2026, 9, 5, 21, 0, tzinfo=timezone.utc)


class OneBuy(Strategy):
    name = "onebuy"
    def __init__(self, portfolio):
        super().__init__(portfolio)
        self.last_close = {}
        self.done = False
    def on_bar(self, bar):
        self.last_close[bar.symbol] = bar.close
        if self.done:
            return []
        self.done = True
        return [OrderIntent(strategy=self.name, strategy_version="v", symbol=bar.symbol,
                            side=Side.BUY, qty=10, ts=bar.bar_ts)]


class RecordingBroker(StubBroker):
    def __init__(self):
        super().__init__()
        self.submitted = []
    async def submit(self, intent, price):
        self.submitted.append(intent)
        return await super().submit(intent, price)


def setup(tmp_path, repo=None):
    settings = Settings(alpaca_paper=True, kill_switch_path=tmp_path / "KILL",
                        db_path=tmp_path / "t.db", event_log_path=tmp_path / "e.jsonl")
    bus = EventBus()
    repo = repo or LedgerRepo(settings.db_path)
    bus.subscribe("*", repo.on_event, critical=True)
    portfolio = Portfolio(cash=100_000.0)
    gate = RiskGate(settings, portfolio, kill_switch_path=settings.kill_switch_path)
    broker = RecordingBroker()
    engine = TradingEngine(bus, portfolio, gate, broker, [OneBuy(portfolio)])
    return bus, repo, portfolio, gate, broker, engine


def bar(close=100.0):
    return MarketBar(symbol="AAA", open=close, high=close, low=close, close=close, volume=1, bar_ts=TS, ts=TS)


async def test_approval_is_recorded_before_submission_and_reservation_released(tmp_path):
    bus, repo, portfolio, gate, broker, engine = setup(tmp_path)
    await bus.publish(bar())
    rows = list(reversed(repo.orders(limit=10)))  # oldest first
    statuses = [r["status"] for r in rows]
    assert statuses[:3] == ["approved", "submitted", "filled"]
    assert len(broker.submitted) == 1
    assert gate.pending == []          # FILLED released the reservation
    assert portfolio.positions["AAA"].qty == 10


async def test_ledger_failure_halts_and_nothing_is_sent(tmp_path):
    class BrokenRepo(LedgerRepo):
        def record(self, event):
            raise RuntimeError("disk full")
    bus, repo, portfolio, gate, broker, engine = setup(tmp_path, repo=BrokenRepo(tmp_path / "b.db"))
    try:
        await bus.publish(bar())
    except Exception:
        pass  # the MarketBar itself failed to persist; the engine still ran its handler first
    assert broker.submitted == []
    assert gate.halted and gate.halt_kind == "infra"
    assert gate.pending == []


async def test_missed_fill_recorded_without_reapplying(tmp_path):
    """Fills ingested at startup go to the ledger only: the broker seed already holds them."""
    from driftline.core.events import Fill
    repo = LedgerRepo(tmp_path / "t.db")
    portfolio = Portfolio(cash=90_000.0)
    fill = Fill(intent_id="i", broker_order_id="b", symbol="AAA", side=Side.BUY, qty=10, price=1000.0,
                fee=0.0, strategy="s", strategy_version="v", ts=TS)
    repo.record(fill)
    assert repo.fills(limit=5)[0]["symbol"] == "AAA"
    assert "AAA" not in portfolio.positions  # not applied
    assert repo.ledger_positions() == {"AAA": 10.0}


async def test_non_terminal_intents_listed(tmp_path):
    from driftline.core.events import OrderUpdate
    repo = LedgerRepo(tmp_path / "t.db")
    common = dict(intent_id="i1", symbol="AAA", side=Side.BUY, qty=5, strategy="s", strategy_version="v", ts=TS)
    repo.record(OrderUpdate(broker_order_id="", status=OrderStatus.APPROVED, **common))
    repo.record(OrderUpdate(broker_order_id="o1", status=OrderStatus.SUBMITTED, **common))
    assert [i["intent_id"] for i in repo.non_terminal_intents()] == ["i1"]
    repo.record(OrderUpdate(broker_order_id="o1", status=OrderStatus.FILLED, **common))
    assert repo.non_terminal_intents() == []
