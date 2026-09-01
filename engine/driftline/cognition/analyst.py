"""Analyst job: read fresh earnings 8-Ks from EDGAR, publish drift signals.

For each unprocessed earnings 8-K on the watchlist, fetch the filing text
(press release included), ask Claude for a structured EarningsAssessment,
and publish a bounded `earnings` ResearchSignal. The earnings-drift strategy
consumes them through the SignalStore. Filings are marked in the ledger so
each is analyzed exactly once.

    uv run python -m driftline.cognition.analyst
"""

from __future__ import annotations

import asyncio
import logging

from ..config import settings
from ..core.bus import EventBus
from ..core.events import JournalEntry, ResearchSignal
from ..ledger.repo import LedgerRepo
from ..strategy.watchlist import WATCHLIST
from . import edgar
from .claude import MODEL, make_client, structured_call
from .schemas import EarningsAssessment

log = logging.getLogger(__name__)

SYSTEM = """You are the earnings analyst for Driftline, a long-only swing system \
trading the post-earnings-drift window (entering 0-3 days after results, holding \
about three weeks). You are scoring ONE company's earnings 8-K / press release, \
provided in full below.

Base your assessment strictly on the filing text provided — reported numbers, \
guidance language, segment detail, tone of management commentary. Do not use \
consensus estimates or price action from memory; if the filing doesn't state a \
comparison, infer the surprise only from what it does state (e.g. raised guidance, \
records, growth rates) and lower your confidence. Strong positive scores need BOTH \
good results AND forward-looking strength. Unclear or mixed filings score near 0. \
Your output is a bounded parameter; deterministic code and hard risk limits decide \
all trades."""


async def run_once() -> int:
    if not settings.edgar_contact:
        log.warning("EDGAR_CONTACT not set in .env — analyst skipped")
        return 0

    repo = LedgerRepo(settings.db_path)
    bus = EventBus(log_path=settings.event_log_path)
    bus.subscribe("*", repo.on_event)

    filings = edgar.fetch_recent_earnings_filings(WATCHLIST)
    fresh = [f for f in filings if not repo.filing_seen(f.accession)]
    log.info("EDGAR: %d earnings 8-Ks in window, %d not yet analyzed", len(filings), len(fresh))
    if not fresh:
        bus.close()
        return 0

    client = make_client()
    published = 0
    for filing in fresh:
        try:
            text = edgar.fetch_filing_text(filing)
            if len(text) < 500:
                log.warning("%s %s: filing text too thin, skipping", filing.symbol, filing.accession)
                repo.mark_filing(filing.symbol, filing.form, filing.filed, filing.accession)
                continue
            prompt = (
                f"Company: {filing.symbol}. Form {filing.form} filed {filing.filed}.\n\n"
                f"Filing text:\n{text}\n\nProduce the EarningsAssessment."
            )
            a = structured_call(client, SYSTEM, prompt, EarningsAssessment)
            await bus.publish(ResearchSignal(
                kind="earnings", key=filing.symbol,
                value=max(-1.0, min(1.0, a.score)),
                confidence=a.confidence,
                reasoning=f"[{a.surprise}/{a.guidance}] {a.reasoning}",
                source_model=MODEL,
            ))
            await bus.publish(JournalEntry(
                strategy="analyst", strategy_version=MODEL, kind="earnings",
                text=(f"{filing.symbol} 8-K ({filing.filed}): {a.surprise}, guidance "
                      f"{a.guidance}, score {a.score:+.2f} (conf {a.confidence:.2f}). {a.reasoning}"),
                payload={"accession": filing.accession},
            ))
            repo.mark_filing(filing.symbol, filing.form, filing.filed, filing.accession)
            published += 1
        except Exception:
            log.exception("analysis failed for %s %s; will retry next run", filing.symbol, filing.accession)
    bus.close()
    log.info("published %d earnings signals", published)
    return published


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
