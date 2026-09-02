"""Strategist end-to-end with mocked Claude — would have caught the
live-only AttributeError crash and the silent candidate-dropping."""

import asyncio
import math

from driftline.cognition import strategist
from driftline.cognition.schemas import ParameterCandidate, StrategyProposals
from driftline.core.bus import EventBus
from driftline.core.events import MarketBar, utcnow
from driftline.ledger.repo import LedgerRepo
from driftline.strategy.baseline_momentum import UNIVERSE

PROPOSALS = StrategyProposals(
    market_read="test",
    candidates=[
        ParameterCandidate(lookback=42, top_n=2, target_gross=0.8, rebalance_days=14,
                           hypothesis="shorter lookback"),
        ParameterCandidate(lookback=252, top_n=3, target_gross=0.9, rebalance_days=7,
                           hypothesis="lookback exceeds stored history; must be reported, not dropped"),
    ],
)


async def seed_bars(repo: LedgerRepo, days=200):
    bus = EventBus()
    bus.subscribe("*", repo.on_event)
    from datetime import datetime, timedelta, timezone
    ts = datetime(2026, 1, 5, 21, 0, tzinfo=timezone.utc)
    prices = {s: 100.0 + i * 10 for i, s in enumerate(UNIVERSE)}
    for d in range(days):
        if ts.weekday() < 5:
            for i, s in enumerate(UNIVERSE):
                prices[s] *= math.exp(0.0003 * (i + 1))
                await bus.publish(MarketBar(symbol=s, open=prices[s], high=prices[s],
                                            low=prices[s], close=round(prices[s], 2),
                                            volume=1e6, bar_ts=ts, ts=ts))
        ts += timedelta(days=1)


async def test_strategist_runs_and_reports_skipped(tmp_path, monkeypatch):
    repo = LedgerRepo(tmp_path / "t.db")
    await seed_bars(repo)

    monkeypatch.setattr(strategist.settings, "db_path", tmp_path / "t.db")
    monkeypatch.setattr(strategist.settings, "event_log_path", tmp_path / "e.jsonl")
    monkeypatch.setattr(strategist, "CANDIDATES_DIR", tmp_path / "candidates")
    monkeypatch.setattr(strategist, "make_client", lambda: object())
    monkeypatch.setattr(strategist, "structured_call", lambda *a, **kw: PROPOSALS)
    monkeypatch.setattr(strategist, "write_candidate_branch", lambda *a, **kw: None)

    report_path = await strategist.run_once()
    assert report_path is not None
    report = report_path.read_text()

    # the scorable candidate is scored; the impossible one is reported, not silently dropped
    assert '"lookback": 42' in report
    assert "Not scored" in report and '"lookback": 252' in report
    # baseline appears with train and validation metrics
    assert "Baseline" in report and "validation" in report.lower()
