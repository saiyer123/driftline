"""Cognition daemon: runs the Claude jobs on a schedule.

A separate process from the trading engine, by design — the engine never
makes LLM calls and runs fine if this daemon is down (signals go stale and
the SignalStore decays to neutral defaults).

Schedule (UTC):
  research    weekdays 13:00  (~1.5h before the 13:30/14:30 US open)
  review      weekdays 21:30  (after the close)
  strategist  Saturday 02:00  (weekly, after Friday's bars are stored)

    uv run python -m driftline.cognition.daemon           # run the schedule
    uv run python -m driftline.cognition.daemon --once research|review|strategist
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from . import research, review, strategist

log = logging.getLogger(__name__)

JOBS = {
    "research": research.run_once,
    "review": review.run_once,
    "strategist": strategist.run_once,
}


def next_fire(name: str, now: datetime) -> datetime:
    def at(d: datetime, h: int, m: int) -> datetime:
        return d.replace(hour=h, minute=m, second=0, microsecond=0)

    candidate = now
    for _ in range(9):  # scan forward day by day
        if name == "research" and candidate.weekday() < 5 and at(candidate, 13, 0) > now:
            return at(candidate, 13, 0)
        if name == "review" and candidate.weekday() < 5 and at(candidate, 21, 30) > now:
            return at(candidate, 21, 30)
        if name == "strategist" and candidate.weekday() == 5 and at(candidate, 2, 0) > now:
            return at(candidate, 2, 0)
        candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)
    raise RuntimeError(f"could not schedule {name}")


async def run_job(name: str) -> None:
    log.info("running cognition job: %s", name)
    try:
        await JOBS[name]()
    except Exception:
        # cognition failures never propagate anywhere; the engine doesn't need us
        log.exception("cognition job %s failed; will run again at its next slot", name)


async def run_schedule() -> None:
    while True:
        now = datetime.now(timezone.utc)
        upcoming = {n: next_fire(n, now) for n in JOBS}
        name, when = min(upcoming.items(), key=lambda kv: kv[1])
        wait = (when - now).total_seconds()
        log.info("next job: %s at %s (in %.0f min)", name, when.isoformat(), wait / 60)
        await asyncio.sleep(max(wait, 1))
        await run_job(name)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Driftline cognition daemon")
    parser.add_argument("--once", choices=list(JOBS), help="run one job now and exit")
    args = parser.parse_args()
    if args.once:
        asyncio.run(run_job(args.once))
    else:
        asyncio.run(run_schedule())


if __name__ == "__main__":
    main()
