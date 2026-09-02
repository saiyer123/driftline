"""Halts and the daily-loss baseline must survive engine restarts."""

from datetime import datetime, timezone

from driftline.core.bus import EventBus
from driftline.core.events import EquitySnapshot, HaltEvent, ResumeEvent
from driftline.ledger.repo import LedgerRepo


async def publish(repo, *events):
    bus = EventBus()
    bus.subscribe("*", repo.on_event)
    for e in events:
        await bus.publish(e)


async def test_unresolved_halt_is_open(tmp_path):
    repo = LedgerRepo(tmp_path / "t.db")
    await publish(repo, HaltEvent(source="risk_gate", reason="daily loss"))
    open_halt = repo.open_halt()
    assert open_halt is not None and "daily loss" in open_halt["reason"]


async def test_resumed_halt_is_closed(tmp_path):
    repo = LedgerRepo(tmp_path / "t.db")
    await publish(repo,
                  HaltEvent(source="risk_gate", reason="daily loss"),
                  ResumeEvent(source="manual", reason="investigated"))
    assert repo.open_halt() is None


async def test_day_start_equity_from_first_snapshot(tmp_path):
    repo = LedgerRepo(tmp_path / "t.db")
    today = datetime.now(timezone.utc)
    await publish(repo,
                  EquitySnapshot(equity=100_000, cash=1, gross_exposure=0,
                                 realized_pnl_today=0, unrealized_pnl=0, ts=today),
                  EquitySnapshot(equity=97_000, cash=1, gross_exposure=0,
                                 realized_pnl_today=0, unrealized_pnl=0, ts=today))
    # a mid-day restart must resume the 100k baseline, not adopt 97k
    assert repo.day_start_equity(today.date().isoformat()) == 100_000
