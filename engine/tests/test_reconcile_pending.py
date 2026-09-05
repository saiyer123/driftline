"""Reconciler runs with orders in flight and tolerates exactly what they explain."""

from driftline.config import Settings
from driftline.core.bus import EventBus
from driftline.core.events import OrderIntent, Side
from driftline.portfolio.accounting import Portfolio, Position
from driftline.reconcile import Reconciler
from driftline.risk.gate import RiskGate


class FakeBroker:
    def __init__(self, state):
        self.state = state
    async def account_state(self):
        return self.state


def setup(tmp_path, cash=50_000.0):
    s = Settings(alpaca_paper=True, kill_switch_path=tmp_path / "KILL", db_path=tmp_path / "t.db",
                 event_log_path=tmp_path / "e.jsonl", max_daily_loss_pct=0.03)
    p = Portfolio(cash=cash)
    return EventBus(), p, RiskGate(s, p, kill_switch_path=s.kill_switch_path)


async def test_pending_buy_explains_broker_holding_more(tmp_path):
    bus, p, gate = setup(tmp_path)
    p.positions["SPY"] = Position(symbol="SPY", qty=10, avg_entry=500, mark=500)
    pending = OrderIntent(strategy="s", strategy_version="v", symbol="SPY", side=Side.BUY, qty=5)
    gate.reserve(pending, 500.0)
    # broker already filled the pending 5: qty 15, cash down $2,500
    broker = FakeBroker({"equity": 57_500.0, "cash": 47_500.0, "positions": {"SPY": 15.0}})
    assert await Reconciler(bus, p, gate, broker).check_once() == []
    assert not gate.halted


async def test_difference_beyond_pending_halts_with_reconcile_kind(tmp_path):
    bus, p, gate = setup(tmp_path)
    p.positions["SPY"] = Position(symbol="SPY", qty=10, avg_entry=500, mark=500)
    gate.reserve(OrderIntent(strategy="s", strategy_version="v", symbol="SPY", side=Side.BUY, qty=5), 500.0)
    broker = FakeBroker({"equity": 60_000.0, "cash": 50_000.0, "positions": {"SPY": 22.0}})  # 12 extra, only 5 pending
    problems = await Reconciler(bus, p, gate, broker).check_once()
    assert problems and gate.halted and gate.halt_kind == "reconcile"


async def test_broker_equity_feeds_daily_loss_halt(tmp_path):
    bus, p, gate = setup(tmp_path, cash=100_000.0)
    gate.reset_day(100_000.0)
    broker = FakeBroker({"equity": 96_000.0, "cash": 100_000.0, "positions": {}})
    await Reconciler(bus, p, gate, broker).check_once()
    assert gate.halted and gate.halt_kind == "daily_loss"
