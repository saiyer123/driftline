"""Cognition-plane safety tests: clamps, staleness decay, deterministic backtests.

No test here calls Claude — the LLM boundary is exactly what these tests fence.
"""

from datetime import datetime, timedelta, timezone

from driftline.cognition.backtest import run_backtest
from driftline.config import Settings
from driftline.core.bus import EventBus
from driftline.core.events import ResearchSignal
from driftline.ledger.repo import LedgerRepo
from driftline.portfolio.accounting import Portfolio
from driftline.risk.signal_store import SignalStore
from driftline.strategy.baseline_momentum import BaselineMomentum


def make_repo(tmp_path) -> LedgerRepo:
    return LedgerRepo(tmp_path / "t.db")


async def publish_signal(repo, **kw):
    bus = EventBus()
    bus.subscribe("*", repo.on_event)
    defaults = dict(kind="regime", key="market", value=1.0, confidence=0.8,
                    reasoning="test", source_model="test")
    await bus.publish(ResearchSignal(**{**defaults, **kw}))


# -- SignalStore clamps and staleness ---------------------------------------

async def test_defaults_when_no_signal(tmp_path):
    store = SignalStore(make_repo(tmp_path))
    assert store.risk_appetite() == 1.0
    assert store.symbol_tilt("SPY") == 0.0


async def test_risk_appetite_clamped(tmp_path):
    repo = make_repo(tmp_path)
    store = SignalStore(repo)
    await publish_signal(repo, value=0.05)   # below floor
    assert store.risk_appetite() == 0.3
    await publish_signal(repo, value=7.0)    # a "lever up 7x" signal is ignored
    assert store.risk_appetite() == 1.0


async def test_stale_signal_decays_to_default(tmp_path):
    repo = make_repo(tmp_path)
    store = SignalStore(repo, max_age_hours=36)
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    await publish_signal(repo, value=0.4, ts=old)
    assert store.risk_appetite() == 1.0  # too old — neutral default


async def test_symbol_tilt_clamped(tmp_path):
    repo = make_repo(tmp_path)
    store = SignalStore(repo)
    await publish_signal(repo, kind="symbol_tilt", key="SPY", value=-9.0)
    assert store.symbol_tilt("SPY") == -1.0
    assert store.symbol_tilt("QQQ") == 0.0


async def test_latest_signal_wins(tmp_path):
    repo = make_repo(tmp_path)
    store = SignalStore(repo)
    now = datetime.now(timezone.utc)
    await publish_signal(repo, value=0.5, ts=now - timedelta(hours=2))
    await publish_signal(repo, value=0.9, ts=now)
    assert store.risk_appetite() == 0.9


# -- strategy consumes the bounded parameter --------------------------------

class FixedSignals:
    def __init__(self, appetite):
        self._a = appetite

    def risk_appetite(self):
        return self._a


def test_risk_appetite_scales_target_weights():
    p_full = Portfolio(cash=100_000.0)
    p_derisked = Portfolio(cash=100_000.0)
    s_full = BaselineMomentum(p_full, signals=FixedSignals(1.0))
    s_derisked = BaselineMomentum(p_derisked, signals=FixedSignals(0.5))

    from tests.test_replay_end_to_end import synthetic_bars
    bars = synthetic_bars(120)
    full_intents, derisked_intents = [], []
    for bar in bars:
        full_intents += [e for e in s_full.on_bar(bar) if hasattr(e, "side")]
        derisked_intents += [e for e in s_derisked.on_bar(bar) if hasattr(e, "side")]
    first_full = next(i for i in full_intents if i.side.value == "buy")
    first_derisked = next(i for i in derisked_intents if i.side.value == "buy")
    ratio = first_derisked.qty / first_full.qty
    assert 0.4 < ratio < 0.6  # half the appetite -> about half the position


# -- backtester -------------------------------------------------------------

def trend_closes(days=300, n=4):
    import math
    return {
        f"S{i}": [100 * math.exp((0.0002 + 0.0004 * i) * t) for t in range(days)]
        for i in range(n)
    }


def test_backtest_deterministic():
    closes = trend_closes()
    a = run_backtest(closes, lookback=63, top_n=2, target_gross=0.9, rebalance_days=7)
    b = run_backtest(closes, lookback=63, top_n=2, target_gross=0.9, rebalance_days=7)
    assert a.to_dict() == b.to_dict()


def test_backtest_picks_up_trend():
    r = run_backtest(trend_closes(), lookback=63, top_n=2, target_gross=0.9, rebalance_days=7)
    assert r.total_return > 0
    assert r.trading_days > 200


def test_backtest_insufficient_history():
    closes = {"A": [100.0] * 30, "B": [100.0] * 30}
    assert run_backtest(closes, lookback=63, top_n=1, target_gross=0.9, rebalance_days=7) is None


def test_backtest_charges_turnover():
    # oscillating relative performance so short lookbacks flip the ranking
    import math
    closes = {
        f"S{i}": [100 * math.exp(0.0003 * t + 0.05 * math.sin(t / 10 + i)) for t in range(300)]
        for i in range(4)
    }
    slow = run_backtest(closes, lookback=63, top_n=2, target_gross=0.9, rebalance_days=30)
    fast = run_backtest(closes, lookback=5, top_n=1, target_gross=0.9, rebalance_days=5)
    assert fast.annual_turnover > slow.annual_turnover
