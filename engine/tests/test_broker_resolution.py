"""Alpaca adapter against a fake client: rejection vs ambiguous failures, partial
fills on terminal orders, missed-order ingestion. No network."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from driftline.broker import alpaca_broker as ab
from driftline.core.events import Fill, OrderIntent, OrderStatus, OrderUpdate, Side
from driftline.ledger.repo import LedgerRepo

TS = datetime(2026, 9, 5, 21, 0, tzinfo=timezone.utc)


class Rejection(Exception):
    status_code = 422


class FakeOrder(SimpleNamespace):
    pass


class FakeClient:
    def __init__(self, submit_exc=None, by_client_id=None):
        self.submit_exc = submit_exc
        self.by_client_id = by_client_id or {}
    def submit_order(self, req):
        if self.submit_exc:
            raise self.submit_exc
        return FakeOrder(id="broker-1", client_order_id=req.client_order_id, symbol=req.symbol,
                         side=SimpleNamespace(value="buy"), qty=req.qty, status=SimpleNamespace(value="accepted"),
                         filled_qty=0, filled_avg_price=None)
    def get_order_by_client_id(self, cid):
        if cid in self.by_client_id:
            return self.by_client_id[cid]
        raise Rejection("404 not found")
    def get_orders(self, req):
        return []


def broker(client):
    b = ab.AlpacaBroker.__new__(ab.AlpacaBroker)
    b.client = client
    b._open = {}
    return b


def intent(**kw):
    base = dict(strategy="s", strategy_version="v", symbol="AAA", side=Side.BUY, qty=5.0, ts=TS)
    return OrderIntent(**{**base, **kw})


async def test_definitive_rejection_is_rejected():
    b = broker(FakeClient(submit_exc=Rejection("insufficient buying power")))
    events = await b.submit(intent(), 100.0)
    assert len(events) == 1 and events[0].status == OrderStatus.REJECTED
    assert not b.has_open_orders


async def test_ambiguous_failure_resolved_by_client_id_is_tracked():
    i = intent()
    live = FakeOrder(id="broker-9", client_order_id=i.intent_id, symbol="AAA", side=SimpleNamespace(value="buy"),
                     qty=5.0, status=SimpleNamespace(value="accepted"), filled_qty=0, filled_avg_price=None)
    b = broker(FakeClient(submit_exc=TimeoutError("read timed out"), by_client_id={i.intent_id: live}))
    events = await b.submit(i, 100.0)
    assert events[0].status == OrderStatus.SUBMITTED and events[0].broker_order_id == "broker-9"
    assert b.has_open_orders  # tracked, not resubmitted


async def test_ambiguous_failure_not_found_is_rejected():
    b = broker(FakeClient(submit_exc=TimeoutError("read timed out")))
    events = await b.submit(intent(), 100.0)
    assert events[0].status == OrderStatus.REJECTED and "unresolved" in events[0].reason


def test_canceled_order_with_partial_fill_emits_fill():
    order = FakeOrder(filled_qty=4, filled_avg_price=101.0)
    common = dict(intent_id="i", symbol="AAA", side=Side.BUY, qty=10.0, strategy="s", strategy_version="v")
    events = ab.AlpacaBroker._terminal_events("o1", "canceled", order, common)
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["Fill", "OrderUpdate"]
    assert events[0].qty == 4 and events[1].status == OrderStatus.CANCELED


async def test_resolve_missed_ingests_orders_finished_while_down(tmp_path):
    repo = LedgerRepo(tmp_path / "t.db")
    common = dict(intent_id="i7", symbol="AAA", side=Side.BUY, qty=5.0, strategy="s", strategy_version="v", ts=TS)
    repo.record(OrderUpdate(broker_order_id="", status=OrderStatus.APPROVED, **common))
    repo.record(OrderUpdate(broker_order_id="o7", status=OrderStatus.SUBMITTED, **common))
    done = FakeOrder(id="o7", client_order_id="i7", symbol="AAA", side=SimpleNamespace(value="buy"), qty=5.0,
                     status=SimpleNamespace(value="filled"), filled_qty=5.0, filled_avg_price=100.5)
    b = broker(FakeClient(by_client_id={"i7": done}))
    events = await b.resolve_missed(repo)
    assert [type(e).__name__ for e in events] == ["Fill", "OrderUpdate"]
    for e in events:
        repo.record(e)
    assert repo.non_terminal_intents() == []
    assert repo.ledger_positions() == {"AAA": 5.0}
