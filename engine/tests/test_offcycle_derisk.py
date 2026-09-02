"""Off-cycle de-risk: sharp appetite drops sell down immediately, sells only."""

from datetime import datetime, timedelta, timezone

from driftline.core.events import MarketBar, OrderIntent, Side
from driftline.portfolio.accounting import Portfolio, Position
from driftline.strategy.baseline_momentum import UNIVERSE, BaselineMomentum


class MutableSignals:
    def __init__(self, appetite=1.0):
        self.appetite = appetite

    def risk_appetite(self):
        return self.appetite


def day_bars(day: int, price=100.0):
    ts = datetime(2026, 9, 7, 21, 0, tzinfo=timezone.utc) + timedelta(days=day)
    return [
        MarketBar(symbol=s, open=price, high=price, low=price, close=price,
                  volume=1e6, bar_ts=ts, ts=ts)
        for s in UNIVERSE
    ]


def run_day(strategy, day):
    events = []
    for bar in day_bars(day):
        events += strategy.on_bar(bar)
    return [e for e in events if isinstance(e, OrderIntent)]


def make_strategy(signals):
    p = Portfolio(cash=10_000.0)
    # simulate an existing book sized at appetite 1.0
    for s in ["SPY", "QQQ", "IWM"]:
        p.positions[s] = Position(symbol=s, qty=100, avg_entry=100, mark=100)
    strat = BaselineMomentum(p, signals=signals)
    strat._applied_appetite = 1.0
    strat._last_rebalance = datetime(2026, 9, 7, tzinfo=timezone.utc).date()
    return p, strat


def test_sharp_drop_sells_down_immediately():
    signals = MutableSignals(appetite=1.0)
    p, strat = make_strategy(signals)
    assert run_day(strat, 1) == []          # nothing on a normal day

    signals.appetite = 0.4                   # risk-off shock
    intents = run_day(strat, 2)
    assert intents and all(i.side == Side.SELL for i in intents)
    total_sold = sum(i.qty * 100 for i in intents)
    held = 3 * 100 * 100
    assert 0.5 < total_sold / held < 0.7     # ~60% reduction (1.0 -> 0.4)


def test_small_drop_or_rise_waits_for_rebalance():
    signals = MutableSignals(appetite=1.0)
    _, strat = make_strategy(signals)
    signals.appetite = 0.8                   # drop, but above floor
    assert run_day(strat, 2) == []
    signals.appetite = 0.65                  # below drop threshold? 0.35 drop but above 0.6 floor
    assert run_day(strat, 3) == []


def test_derisk_never_buys_even_when_underweight():
    signals = MutableSignals(appetite=0.3)
    p, strat = make_strategy(signals)
    p.positions["SPY"].qty = 1               # deeply underweight vs any target
    intents = run_day(strat, 2)
    assert all(i.side == Side.SELL for i in intents)
