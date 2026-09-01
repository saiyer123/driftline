"""Earnings-drift module: EDGAR parsing, signal freshness, strategy behavior."""

import json
from datetime import date, datetime, timedelta, timezone

from driftline.cognition.edgar import EarningsFiling, load_cik_map, parse_earnings_8ks, strip_html
from driftline.core.events import MarketBar, OrderIntent, Side
from driftline.portfolio.accounting import Portfolio, Position
from driftline.strategy.earnings_drift import (
    ENTRY_SCORE,
    HOLD_DAYS,
    MAX_CONCURRENT,
    POSITION_WEIGHT,
    EarningsDrift,
)

# -- EDGAR parsing (pure functions, no network) ------------------------------

def test_load_cik_map():
    raw = json.dumps({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                      "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft"}}).encode()
    m = load_cik_map(raw)
    assert m["AAPL"] == 320193 and m["MSFT"] == 789019


def test_parse_earnings_8ks_filters_correctly():
    raw = json.dumps({"filings": {"recent": {
        "form": ["8-K", "8-K", "10-Q", "8-K"],
        "filingDate": ["2026-08-30", "2026-08-30", "2026-08-29", "2026-01-05"],
        "items": ["2.02,9.01", "5.02", "", "2.02"],
        "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-26-000003", "0001-26-000004"],
        "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm"],
    }}}).encode()
    out = parse_earnings_8ks("AAPL", 320193, raw, since=date(2026, 8, 1))
    assert len(out) == 1  # only the recent 8-K with item 2.02
    assert out[0].accession == "000126000001"
    assert out[0].primary_doc == "a.htm"


def test_strip_html():
    raw = "<html><style>x{}</style><body><h1>Q3 &amp; Results</h1><p>Revenue  up</p></body></html>"
    text = strip_html(raw)
    assert "Q3 & Results" in text and "Revenue up" in text
    assert "<" not in text


# -- strategy behavior -------------------------------------------------------

class FakeSignals:
    def __init__(self, events=None):
        self.events = events or {}

    def earnings_event(self, symbol, max_age_hours=96):
        return self.events.get(symbol)

    def risk_appetite(self):
        return 1.0


def bar(symbol="AAPL", close=200.0, day=0):
    ts = datetime(2026, 9, 1, 21, 0, tzinfo=timezone.utc) + timedelta(days=day)
    return MarketBar(symbol=symbol, open=close, high=close, low=close,
                     close=close, volume=1e6, bar_ts=ts, ts=ts)


def good_signal():
    return {"score": 0.8, "confidence": 0.7, "ts": "2026-09-01T12:00:00+00:00"}


def intents(events):
    return [e for e in events if isinstance(e, OrderIntent)]


def test_enters_on_strong_signal():
    p = Portfolio(cash=100_000.0)
    s = EarningsDrift(p, FakeSignals({"AAPL": good_signal()}))
    out = intents(s.on_bar(bar()))
    assert len(out) == 1 and out[0].side == Side.BUY
    assert abs(out[0].qty * 200.0 - POSITION_WEIGHT * 100_000) < 1


def test_ignores_weak_or_negative_signals():
    p = Portfolio(cash=100_000.0)
    weak = {"score": ENTRY_SCORE - 0.1, "confidence": 0.9, "ts": "x"}
    negative = {"score": -0.9, "confidence": 0.9, "ts": "x"}  # avoided, never shorted
    s = EarningsDrift(p, FakeSignals({"AAPL": weak, "MSFT": negative}))
    assert not intents(s.on_bar(bar("AAPL")))
    assert not intents(s.on_bar(bar("MSFT")))


def test_exits_after_hold_days_and_is_idempotent():
    p = Portfolio(cash=100_000.0)
    s = EarningsDrift(p, FakeSignals({"AAPL": good_signal()}))
    out = intents(s.on_bar(bar(day=0)))
    assert out  # entered
    p.positions["AAPL"] = Position(symbol="AAPL", qty=out[0].qty, avg_entry=200, mark=200)

    sells = []
    for day in range(1, HOLD_DAYS + 2):
        sells += intents(s.on_bar(bar(day=day)))
    assert sells and all(i.side == Side.SELL for i in sells)
    # vetoed sell -> position still held -> re-emits next bar
    more = intents(s.on_bar(bar(day=HOLD_DAYS + 2)))
    assert more and more[0].side == Side.SELL
    # once flat, state clears and no further sells
    p.positions["AAPL"].qty = 0.0
    assert not intents(s.on_bar(bar(day=HOLD_DAYS + 3)))
    assert "AAPL" not in s.entries


def test_vetoed_entry_self_heals():
    p = Portfolio(cash=100_000.0)
    s = EarningsDrift(p, FakeSignals({"AAPL": good_signal()}))
    assert intents(s.on_bar(bar(day=0)))  # entry emitted but never fills
    s.on_bar(bar(day=1))                  # next day: no position -> entry dropped
    assert "AAPL" not in s.entries
    assert intents(s.on_bar(bar(day=1)))  # free to try again while signal is fresh


def test_max_concurrent_cap():
    p = Portfolio(cash=1_000_000.0)
    symbols = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL"]
    s = EarningsDrift(p, FakeSignals({sym: good_signal() for sym in symbols}))
    entered = [sym for sym in symbols if intents(s.on_bar(bar(sym)))]
    assert len(entered) == MAX_CONCURRENT


def test_ignores_non_watchlist_symbols():
    p = Portfolio(cash=100_000.0)
    s = EarningsDrift(p, FakeSignals({"SPY": good_signal()}))
    assert s.on_bar(bar("SPY")) == []  # ETF universe is not this strategy's turf


async def test_rebuilds_entries_from_ledger(tmp_path):
    from driftline.ledger.repo import LedgerRepo
    from driftline.core.bus import EventBus
    from driftline.core.events import Fill

    repo = LedgerRepo(tmp_path / "t.db")
    bus = EventBus()
    bus.subscribe("*", repo.on_event)
    fill = Fill(intent_id="i", broker_order_id="b", symbol="AAPL", side=Side.BUY,
                qty=25.0, price=200.0, fee=0.0, strategy="earnings_drift",
                strategy_version="v")
    await bus.publish(fill)

    p = Portfolio(cash=100_000.0)
    p.positions["AAPL"] = Position(symbol="AAPL", qty=25.0, avg_entry=200, mark=200)
    s = EarningsDrift(p, FakeSignals(), repo=repo)
    assert "AAPL" in s.entries
