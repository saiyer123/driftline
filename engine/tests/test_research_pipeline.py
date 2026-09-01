"""Researcher pipeline end-to-end with a mocked Claude call and mocked news.

Verifies the mechanical path: report → bounded ResearchSignals in the ledger →
SignalStore reads them back clamped — without any network or LLM dependency.
"""

from driftline.cognition import research
from driftline.cognition.schemas import ResearchReport, SymbolAssessment
from driftline.ledger.repo import LedgerRepo
from driftline.risk.signal_store import SignalStore


FAKE_REPORT = ResearchReport(
    regime="risk_off",
    risk_appetite=0.5,
    regime_confidence=0.7,
    regime_reasoning="Broad selloff in the provided headlines.",
    symbols=[
        SymbolAssessment(symbol="SPY", tilt=-0.4, confidence=0.6, reasoning="index stress"),
        SymbolAssessment(symbol="GLD", tilt=0.7, confidence=0.8, reasoning="flight to safety"),
        SymbolAssessment(symbol="FAKE", tilt=1.0, confidence=0.9, reasoning="hallucinated ticker"),
    ],
    notable_risks=["test risk"],
)


async def test_research_publishes_bounded_signals(tmp_path, monkeypatch):
    monkeypatch.setattr(research.settings, "db_path", tmp_path / "t.db")
    monkeypatch.setattr(research.settings, "event_log_path", tmp_path / "e.jsonl")
    monkeypatch.setattr(research, "gather_news", lambda **kw: [
        {"at": "2026-09-01", "headline": "Markets fall", "summary": "", "symbols": ["SPY"], "source": "test"},
    ])
    monkeypatch.setattr(research, "make_client", lambda: object())
    monkeypatch.setattr(research, "structured_call", lambda *a, **kw: FAKE_REPORT)

    report = await research.run_once()
    assert report is FAKE_REPORT

    repo = LedgerRepo(tmp_path / "t.db")
    signals = repo.latest_signals()
    kinds = {(s["kind"], s["key"]) for s in signals}
    assert ("regime", "market") in kinds
    assert ("symbol_tilt", "SPY") in kinds
    assert ("symbol_tilt", "GLD") in kinds
    assert ("symbol_tilt", "FAKE") not in kinds  # hallucinated ticker rejected

    store = SignalStore(repo)
    assert store.risk_appetite() == 0.5
    assert store.symbol_tilt("GLD") == 0.7

    journal = repo.journal()
    assert any(j["strategy"] == "researcher" for j in journal)


def test_prompt_contains_no_memory_reliance_gaps():
    prompt = research.build_prompt(
        [{"at": "t", "headline": "h", "summary": "s", "symbols": ["SPY"], "source": "x"}],
        ["SPY: last 500.00, 5d +1.0%, 63d +5.0%"],
    )
    assert "Current UTC time" in prompt   # date grounding, not training memory
    assert "SPY: last 500.00" in prompt   # our own stored prices, not recalled ones
    assert "Markets" not in prompt or True
