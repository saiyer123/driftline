from driftline.config import Settings
from driftline.core.bus import EventBus
from driftline.portfolio.accounting import Portfolio, Position
from driftline.reconcile import Reconciler
from driftline.risk.gate import RiskGate


class FakeBroker:
    def __init__(self, state):
        self.state = state

    async def account_state(self):
        return self.state


def setup(tmp_path, cash=50_000.0):
    settings = Settings(alpaca_paper=True, kill_switch_path=tmp_path / "KILL",
                        db_path=tmp_path / "t.db", event_log_path=tmp_path / "e.jsonl")
    bus = EventBus()
    portfolio = Portfolio(cash=cash)
    gate = RiskGate(settings, portfolio, kill_switch_path=settings.kill_switch_path)
    return bus, portfolio, gate


async def test_clean_reconcile_passes(tmp_path):
    bus, portfolio, gate = setup(tmp_path)
    portfolio.positions["SPY"] = Position(symbol="SPY", qty=10, avg_entry=500, mark=500)
    broker = FakeBroker({"equity": 55_000.0, "cash": 50_000.0, "positions": {"SPY": 10.0}})
    problems = await Reconciler(bus, portfolio, gate, broker).check_once()
    assert problems == []
    assert not gate.halted


async def test_position_divergence_halts(tmp_path):
    bus, portfolio, gate = setup(tmp_path)
    portfolio.positions["SPY"] = Position(symbol="SPY", qty=10, avg_entry=500, mark=500)
    # someone traded in the broker UI: broker says 12 shares
    broker = FakeBroker({"equity": 56_000.0, "cash": 50_000.0, "positions": {"SPY": 12.0}})
    problems = await Reconciler(bus, portfolio, gate, broker).check_once()
    assert problems and gate.halted
    assert "SPY" in gate.halt_reason


async def test_cash_divergence_halts(tmp_path):
    bus, portfolio, gate = setup(tmp_path, cash=50_000.0)
    broker = FakeBroker({"equity": 50_000.0, "cash": 48_000.0, "positions": {}})
    problems = await Reconciler(bus, portfolio, gate, broker).check_once()
    assert problems and gate.halted
    assert "cash" in gate.halt_reason
