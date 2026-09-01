from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from driftline.config import Settings
from driftline.core.events import OrderIntent, Side
from driftline.portfolio.accounting import Portfolio, Position
from driftline.risk.gate import RiskGate

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)


def make_gate(tmp_path: Path, cash=100_000.0, **overrides) -> tuple[RiskGate, Portfolio]:
    s = Settings(
        alpaca_paper=True,
        kill_switch_path=tmp_path / "KILL",
        db_path=tmp_path / "test.db",
        event_log_path=tmp_path / "events.jsonl",
        **overrides,
    )
    p = Portfolio(cash=cash)
    return RiskGate(s, p, kill_switch_path=s.kill_switch_path), p


def intent(symbol="SPY", side=Side.BUY, qty=10.0) -> OrderIntent:
    return OrderIntent(strategy="t", strategy_version="v", symbol=symbol, side=side, qty=qty, ts=NOW)


def test_allows_reasonable_buy(tmp_path):
    gate, _ = make_gate(tmp_path)
    d = gate.check(intent(qty=10), price=500.0, now=NOW)
    assert d.allowed, d.reason


def test_vetoes_position_size_limit(tmp_path):
    gate, _ = make_gate(tmp_path, max_position_pct=0.15)  # 15% of 100k = 15k max per symbol
    d = gate.check(intent(qty=40), price=500.0, now=NOW)  # $20k
    assert not d.allowed and "position limit" in d.reason


def test_position_limit_counts_existing_holding(tmp_path):
    gate, p = make_gate(tmp_path, max_position_pct=0.15)
    p.positions["SPY"] = Position(symbol="SPY", qty=20, avg_entry=500, mark=500)  # $10k held
    d = gate.check(intent(qty=15), price=500.0, now=NOW)  # +$7.5k -> $17.5k > $15k
    assert not d.allowed and "position limit" in d.reason


def test_vetoes_gross_exposure(tmp_path):
    gate, p = make_gate(tmp_path, max_position_pct=1.0, max_gross_exposure_pct=0.5)
    p.positions["QQQ"] = Position(symbol="QQQ", qty=100, avg_entry=400, mark=400)  # $40k gross
    d = gate.check(intent(qty=25), price=500.0, now=NOW)  # +12.5k -> 52.5k > 50% of ~140k? equity=cash100k+40k=140k, cap 70k -> allowed
    assert d.allowed
    d2 = gate.check(intent(qty=70), price=500.0, now=NOW)  # +35k -> 75k > 70k
    assert not d2.allowed and "gross exposure" in d2.reason


def test_vetoes_short_sale(tmp_path):
    gate, _ = make_gate(tmp_path)
    d = gate.check(intent(side=Side.SELL, qty=1), price=500.0, now=NOW)
    assert not d.allowed and "long-only" in d.reason


def test_allows_closing_sell(tmp_path):
    gate, p = make_gate(tmp_path)
    p.positions["SPY"] = Position(symbol="SPY", qty=10, avg_entry=400, mark=500)
    d = gate.check(intent(side=Side.SELL, qty=10), price=500.0, now=NOW)
    assert d.allowed


def test_vetoes_insufficient_cash(tmp_path):
    gate, p = make_gate(tmp_path, cash=1_000.0, max_position_pct=1.0, max_gross_exposure_pct=2.0)
    p.positions["QQQ"] = Position(symbol="QQQ", qty=100, avg_entry=400, mark=400)  # equity 41k
    d = gate.check(intent(qty=10), price=500.0, now=NOW)  # $5k order, $1k cash
    assert not d.allowed and "insufficient cash" in d.reason


def test_kill_switch_file(tmp_path):
    gate, _ = make_gate(tmp_path)
    (tmp_path / "KILL").touch()
    d = gate.check(intent(), price=500.0, now=NOW)
    assert not d.allowed and "kill switch" in d.reason


def test_kill_and_resume(tmp_path):
    gate, _ = make_gate(tmp_path)
    gate.kill()
    assert not gate.check(intent(), price=500.0, now=NOW).allowed
    gate.resume()
    assert gate.check(intent(), price=500.0, now=NOW).allowed


def test_daily_loss_halts(tmp_path):
    gate, _ = make_gate(tmp_path)
    gate.observe_equity(100_000, now=NOW)
    gate.observe_equity(96_900, now=NOW + timedelta(hours=1))  # -3.1%
    assert gate.halted
    d = gate.check(intent(), price=500.0, now=NOW + timedelta(hours=1))
    assert not d.allowed and "halted" in d.reason


def test_daily_loss_resets_next_day(tmp_path):
    gate, _ = make_gate(tmp_path)
    gate.observe_equity(100_000, now=NOW)
    gate.observe_equity(98_000, now=NOW + timedelta(hours=1))  # -2%, fine
    assert not gate.halted
    gate.observe_equity(98_000, now=NOW + timedelta(days=1))   # new day baseline
    gate.observe_equity(96_500, now=NOW + timedelta(days=1, hours=1))  # -1.5% on the day
    assert not gate.halted


def test_order_rate_limit(tmp_path):
    gate, _ = make_gate(tmp_path, max_orders_per_minute=3)
    for _ in range(3):
        assert gate.check(intent(qty=1), price=500.0, now=NOW).allowed
    d = gate.check(intent(qty=1), price=500.0, now=NOW)
    assert not d.allowed and "rate limit" in d.reason


def test_rejects_bad_inputs(tmp_path):
    gate, _ = make_gate(tmp_path)
    assert not gate.check(intent(qty=0), price=500.0, now=NOW).allowed
    assert not gate.check(intent(qty=-5), price=500.0, now=NOW).allowed
    assert not gate.check(intent(qty=1), price=0.0, now=NOW).allowed
