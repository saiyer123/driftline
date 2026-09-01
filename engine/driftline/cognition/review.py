"""Reviewer job: post-market post-mortem written into the journal.

Reads the day's ledger (fills, orders incl. vetoes, equity curve, halts,
signals) and asks Claude for a structured DailyReview. Output is a journal
entry only — the reviewer observes and narrates; it changes nothing.

    uv run python -m driftline.cognition.review
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from ..config import settings
from ..core.bus import EventBus
from ..core.events import JournalEntry
from ..ledger.repo import LedgerRepo
from .claude import MODEL, make_client, structured_call
from .schemas import DailyReview

log = logging.getLogger(__name__)

SYSTEM = """You are the end-of-day reviewer for Driftline, a long-only paper-trading \
swing system. Write an honest, plain-language post-mortem of the trading day from \
the ledger data provided. Praise nothing that isn't in the data. Flag anomalies: \
vetoed orders, halts, fills far from expectations, fee drag, concentration. \
A quiet day with no trades is a fine outcome — say so rather than inventing drama. \
Judge process (did the system follow its rules?) over outcome (did today make money?)."""


def today_slice(rows: list[dict]) -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    return [r for r in rows if r["ts"][:10] == today]


async def run_once() -> DailyReview | None:
    repo = LedgerRepo(settings.db_path)
    bus = EventBus(log_path=settings.event_log_path)
    bus.subscribe("*", repo.on_event)

    curve = repo.equity_curve(since_hours=48)
    data = {
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "orders_today": today_slice(repo.orders(limit=300)),
        "fills_today": today_slice(repo.fills(limit=300)),
        "halts_recent": repo.halts(limit=10),
        "signals_current": repo.latest_signals(),
        "equity_start_of_window": curve[0] if curve else None,
        "equity_latest": curve[-1] if curve else None,
    }
    prompt = (
        "Ledger data for today's review (JSON):\n" + json.dumps(data, indent=1) +
        "\n\nWrite the DailyReview."
    )

    client = make_client()
    review = structured_call(client, SYSTEM, prompt, DailyReview)

    await bus.publish(JournalEntry(
        strategy="reviewer", strategy_version=MODEL, kind="review",
        text=(
            review.summary
            + (f"\nWorked: {'; '.join(review.what_worked)}" if review.what_worked else "")
            + (f"\nWatch: {'; '.join(review.what_to_watch)}" if review.what_to_watch else "")
            + f"\nDiscipline: {review.discipline_check}"
        ),
        payload={"orders_today": len(data["orders_today"]), "fills_today": len(data["fills_today"])},
    ))
    bus.close()
    log.info("daily review written to journal")
    return review


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
