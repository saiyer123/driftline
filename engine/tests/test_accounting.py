from datetime import datetime, timedelta, timezone

from driftline.core.events import Fill, MarketBar, Side
from driftline.portfolio.accounting import Portfolio

TS = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)


def fill(symbol="SPY", side=Side.BUY, qty=10.0, price=100.0, fee=0.0, ts=TS) -> Fill:
    return Fill(intent_id="i", broker_order_id="b", symbol=symbol, side=side,
                qty=qty, price=price, fee=fee, strategy="t", strategy_version="v", ts=ts)


def bar(symbol="SPY", close=100.0) -> MarketBar:
    return MarketBar(symbol=symbol, open=close, high=close, low=close,
                     close=close, volume=1000, bar_ts=TS, ts=TS)


def test_buy_updates_cash_and_avg_cost():
    p = Portfolio(cash=10_000)
    p.apply_fill(fill(qty=10, price=100))
    p.apply_fill(fill(qty=10, price=110))
    pos = p.positions["SPY"]
    assert pos.qty == 20
    assert pos.avg_entry == 105
    assert p.cash == 10_000 - 1000 - 1100


def test_sell_realizes_pnl():
    p = Portfolio(cash=10_000)
    p.apply_fill(fill(qty=10, price=100))
    p.apply_fill(fill(side=Side.SELL, qty=10, price=120, fee=1.0))
    assert p.positions["SPY"].qty == 0
    assert p.realized_pnl_today == (120 - 100) * 10 - 1.0
    assert p.cash == 10_000 - 1000 + 1200 - 1.0


def test_realized_pnl_rolls_daily():
    p = Portfolio(cash=10_000)
    p.apply_fill(fill(qty=10, price=100))
    p.apply_fill(fill(side=Side.SELL, qty=5, price=110))
    assert p.realized_pnl_today == 50
    p.apply_fill(fill(side=Side.SELL, qty=5, price=110, ts=TS + timedelta(days=1)))
    assert p.realized_pnl_today == 50  # only the new day's realized


def test_marks_and_unrealized():
    p = Portfolio(cash=10_000)
    p.apply_fill(fill(qty=10, price=100))
    p.apply_mark(bar(close=115))
    assert p.unrealized_pnl == 150
    assert p.equity == 10_000 - 1000 + 1150
    assert p.gross_exposure == 1150


def test_equity_conserved_on_costless_roundtrip():
    p = Portfolio(cash=10_000)
    p.apply_fill(fill(qty=10, price=100))
    p.apply_fill(fill(side=Side.SELL, qty=10, price=100))
    assert p.equity == 10_000
    assert p.realized_pnl_today == 0


def test_fees_reduce_equity():
    p = Portfolio(cash=10_000)
    p.apply_fill(fill(qty=10, price=100, fee=2.0))
    p.apply_fill(fill(side=Side.SELL, qty=10, price=100, fee=2.0))
    assert p.equity == 10_000 - 4.0
