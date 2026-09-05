"""Gate: pending-order reservations and halt kinds (owner-authorized 2026-09-05)."""

from datetime import datetime, timezone

from driftline.config import Settings
from driftline.core.events import OrderIntent, Side
from driftline.portfolio.accounting import Portfolio, Position
from driftline.risk.gate import RiskGate

NOW = datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc)


def make(tmp_path, cash=100_000.0, **over):
    s = Settings(alpaca_paper=True, kill_switch_path=tmp_path / "KILL", db_path=tmp_path / "t.db",
                 event_log_path=tmp_path / "e.jsonl", max_position_pct=0.35, max_gross_exposure_pct=1.0,
                 max_daily_loss_pct=0.03, **over)
    p = Portfolio(cash=cash)
    return RiskGate(s, p, kill_switch_path=s.kill_switch_path), p


def intent(symbol, side=Side.BUY, qty=10.0):
    return OrderIntent(strategy="t", strategy_version="v", symbol=symbol, side=side, qty=qty, ts=NOW)


def test_pending_buys_consume_cash_and_gross(tmp_path):
    gate, _ = make(tmp_path)
    approved = 0
    for sym in ("AAA", "BBB", "CCC", "DDD"):
        i = intent(sym, qty=300)  # $30,000 each at $100
        d = gate.check(i, 100.0, now=NOW)
        if d.allowed:
            gate.reserve(i, 100.0)
            approved += 1
        else:
            assert "insufficient cash" in d.reason or "gross exposure" in d.reason
    assert approved == 3  # the fourth $30k buy cannot pass against $100k with $90k reserved
    # a terminal update releases the reservation and the fourth passes
    gate.release(gate.pending[0].intent_id)
    assert gate.check(intent("DDD", qty=300), 100.0, now=NOW).allowed


def test_pending_buy_counts_toward_position_limit(tmp_path):
    gate, _ = make(tmp_path)
    first = intent("AAA", qty=200)  # $20k
    assert gate.check(first, 100.0, now=NOW).allowed
    gate.reserve(first, 100.0)
    d = gate.check(intent("AAA", qty=200), 100.0, now=NOW)  # another $20k -> $40k > 35% of $100k
    assert not d.allowed and "position limit" in d.reason and "pending" in d.reason


def test_pending_sell_reduces_sellable_quantity(tmp_path):
    gate, p = make(tmp_path)
    p.positions["AAA"] = Position(symbol="AAA", qty=10, avg_entry=100, mark=100)
    s1 = intent("AAA", Side.SELL, qty=6)
    assert gate.check(s1, 100.0, now=NOW).allowed
    gate.reserve(s1, 100.0)
    d = gate.check(intent("AAA", Side.SELL, qty=6), 100.0, now=NOW)  # only 4 left to sell
    assert not d.allowed and "long-only" in d.reason and "pending" in d.reason
    assert gate.check(intent("AAA", Side.SELL, qty=4), 100.0, now=NOW).allowed


def test_daily_loss_halt_allows_risk_reducing_sells_only(tmp_path):
    gate, p = make(tmp_path)
    p.positions["AAA"] = Position(symbol="AAA", qty=10, avg_entry=100, mark=95)
    gate.observe_equity(100_000, now=NOW)
    gate.observe_equity(96_000, now=NOW)  # -4% -> daily_loss halt
    assert gate.halted and gate.halt_kind == "daily_loss"
    assert gate.check(intent("AAA", Side.SELL, qty=10), 95.0, now=NOW).allowed        # protective exit gets through
    assert not gate.check(intent("AAA", Side.SELL, qty=11), 95.0, now=NOW).allowed    # not an over-sell
    assert not gate.check(intent("BBB", Side.BUY, qty=1), 95.0, now=NOW).allowed      # no new risk


def test_reconcile_and_infra_halts_block_everything(tmp_path):
    for kind in ("reconcile", "infra", "manual"):
        gate, p = make(tmp_path)
        p.positions["AAA"] = Position(symbol="AAA", qty=10, avg_entry=100, mark=95)
        gate.halt("book in doubt", kind=kind)
        d = gate.check(intent("AAA", Side.SELL, qty=10), 95.0, now=NOW)
        assert not d.allowed and kind in d.reason


def test_kill_switch_blocks_sells_too(tmp_path):
    gate, p = make(tmp_path)
    p.positions["AAA"] = Position(symbol="AAA", qty=10, avg_entry=100, mark=95)
    gate.kill()
    assert not gate.check(intent("AAA", Side.SELL, qty=10), 95.0, now=NOW).allowed


def test_fill_consumes_reservation_partially_then_fully(tmp_path):
    gate, _ = make(tmp_path)
    i = intent("AAA", qty=100)
    assert gate.check(i, 100.0, now=NOW).allowed
    gate.reserve(i, 100.0)
    gate.consume(i.intent_id, 40)
    r = gate.pending[0]
    assert abs(r.qty - 60) < 1e-9 and abs(r.notional - 6_000) < 1e-6
    gate.consume(i.intent_id, 60)
    assert gate.pending == []
