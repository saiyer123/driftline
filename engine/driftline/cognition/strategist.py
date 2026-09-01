"""Strategist job: Claude proposes, the backtester disposes, a human decides.

Nightly flow:
  1. Load the ledger's stored real daily bars and split walk-forward
     (first ~70% train, last ~30% validation — the validation window is
     never shown to Claude).
  2. Backtest the CURRENT live parameters as the baseline.
  3. Ask Claude for parameter candidates (it sees train-window summary
     stats and the baseline's train metrics only).
  4. Backtest every candidate deterministically on train AND validation.
  5. Write candidates/report-<date>.md ranking them, and a journal entry.

Nothing is applied automatically: promoting a candidate means a human edits
strategy/baseline_momentum.py constants (reviewed like any code change).

    uv run python -m driftline.cognition.strategist
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import subprocess

from ..config import REPO_ROOT, settings
from ..core.bus import EventBus
from ..core.events import JournalEntry
from ..ledger.repo import LedgerRepo
from ..strategy import params as live_params
from ..strategy.baseline_momentum import UNIVERSE  # noqa: F401  (universe source)
from .backtest import run_backtest
from .claude import MODEL, make_client, structured_call
from .schemas import StrategyProposals

log = logging.getLogger(__name__)

CANDIDATES_DIR = REPO_ROOT / "candidates"
MIN_DAYS = 120            # need at least this much stored history to bother
MIN_VALIDATION_DAYS = 100  # branch-worthy evidence needs a real validation window

PARAMS_TEMPLATE = '''"""Tunable parameters for the baseline momentum rotation.

This file is the unit of strategy evolution: the strategist proposes new
values as a `candidate/<date>` git branch editing ONLY this file, backed by
its walk-forward report. Promotion = a human reviews and merges the branch
(then restarts the engine). Never edited by automation on main.
"""

LOOKBACK = {lookback}        # momentum lookback, trading days
TOP_N = {top_n}            # ETFs held
TARGET_GROSS = {target_gross}   # target gross exposure fraction (pre risk-appetite scaling)
REBALANCE_DAYS = {rebalance_days}   # minimum days between rebalances
'''


def write_candidate_branch(params: dict, report_src: "Path", when: str,
                           repo_root: "Path" = REPO_ROOT) -> str | None:
    """Create branch candidate/<date> editing only strategy params + the report.

    Uses a temporary git worktree so the human's working tree is never touched.
    Returns the branch name, or None if it already exists / git fails.
    """
    branch = f"candidate/{when}"
    exists = subprocess.run(["git", "rev-parse", "--verify", "--quiet", branch],
                            cwd=repo_root, capture_output=True)
    if exists.returncode == 0:
        log.info("branch %s already exists; not recreating", branch)
        return None
    wt = repo_root / ".git" / "candidate-worktree"
    try:
        subprocess.run(["git", "worktree", "add", "-b", branch, str(wt), "HEAD"],
                       cwd=repo_root, check=True, capture_output=True)
        params_file = wt / "engine" / "driftline" / "strategy" / "params.py"
        params_file.write_text(PARAMS_TEMPLATE.format(**params))
        dest_report = wt / "candidates" / report_src.name
        dest_report.parent.mkdir(exist_ok=True)
        dest_report.write_text(report_src.read_text())
        subprocess.run(["git", "add", "-A"], cwd=wt, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=driftline-strategist",
             "-c", "user.email=strategist@driftline.local",
             "commit", "-m",
             f"candidate {when}: params {json.dumps(params)}\n\n"
             f"Proposed by the strategist; walk-forward report in candidates/{report_src.name}.\n"
             f"Human review required — merge only after reading the report."],
            cwd=wt, check=True, capture_output=True,
        )
        return branch
    except subprocess.CalledProcessError as exc:
        log.error("candidate branch creation failed: %s", exc.stderr)
        return None
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                       cwd=repo_root, capture_output=True)

SYSTEM = """You are the strategist for Driftline, a long-only weekly ETF momentum \
rotation. Propose parameter candidates for the deterministic backtester to \
evaluate. You see only summary statistics of the training window — the system \
holds out a validation window you never see, so overfit proposals will be caught. \
Propose a DIVERSE set (vary lookback, concentration, cadence, exposure), not \
eight neighbors of one idea. Turnover is expensive: favor candidates that trade \
less unless the data argues otherwise."""


def load_closes(repo: LedgerRepo) -> dict[str, list[float]]:
    closes: dict[str, list[float]] = {}
    for symbol in live_params.UNIVERSE:
        bars = repo.daily_bars(symbol, limit=1000)
        if bars:
            closes[symbol] = [b["close"] for b in bars]
    if not closes:
        return {}
    days = min(len(v) for v in closes.values())
    return {s: v[-days:] for s, v in closes.items()}


def split(closes: dict[str, list[float]], train_frac: float = 0.7):
    days = min(len(v) for v in closes.values())
    cut = int(days * train_frac)
    train = {s: v[:cut] for s, v in closes.items()}
    valid = {s: v[cut - 70:] for s, v in closes.items()}  # overlap so lookbacks warm up
    return train, valid


async def run_once() -> Path | None:
    repo = LedgerRepo(settings.db_path)
    bus = EventBus(log_path=settings.event_log_path)
    bus.subscribe("*", repo.on_event)

    closes = load_closes(repo)
    days = min((len(v) for v in closes.values()), default=0)
    if days < MIN_DAYS:
        log.warning("only %d days of stored bars (< %d) — let the ledger accumulate more history", days, MIN_DAYS)
        return None
    train, valid = split(closes)

    current = dict(lookback=live_params.LOOKBACK, top_n=live_params.TOP_N,
                   target_gross=live_params.TARGET_GROSS,
                   rebalance_days=live_params.REBALANCE_DAYS)
    base_train = run_backtest(train, **current)
    base_valid = run_backtest(valid, **current)

    train_summary = {
        s: {"days": len(v), "total_return": round(v[-1] / v[0] - 1, 4)}
        for s, v in train.items()
    }
    prompt = (
        f"As of {datetime.now(timezone.utc).date()}.\n"
        f"Universe: {', '.join(sorted(closes))}. Training window per-symbol summary:\n"
        + json.dumps(train_summary, indent=1)
        + f"\n\nCurrent live parameters: {json.dumps(current)}"
        + f"\nBaseline metrics on the training window: {json.dumps(base_train.to_dict() if base_train else None)}"
        + "\n\nPropose your StrategyProposals."
    )
    client = make_client()
    proposals = structured_call(client, SYSTEM, prompt, StrategyProposals)

    rows = []
    for c in proposals.candidates:
        params = dict(lookback=c.lookback, top_n=c.top_n,
                      target_gross=c.target_gross, rebalance_days=c.rebalance_days)
        t = run_backtest(train, **params)
        v = run_backtest(valid, **params)
        if t and v:
            rows.append({"params": params, "hypothesis": c.hypothesis,
                         "train": t.to_dict(), "valid": v.to_dict()})
    rows.sort(key=lambda r: r["valid"]["sharpe"], reverse=True)

    best = rows[0] if rows else None
    beats_baseline = bool(
        best and base_valid and best["valid"]["sharpe"] > base_valid.sharpe
    )

    CANDIDATES_DIR.mkdir(exist_ok=True)
    report_path = CANDIDATES_DIR / f"report-{datetime.now(timezone.utc).date()}.md"
    lines = [
        f"# Strategist report — {datetime.now(timezone.utc).date()}",
        "",
        f"Market read: {proposals.market_read}",
        "",
        f"Stored history: {days} trading days. Walk-forward split 70/30; validation window never shown to Claude.",
        f"Costs: 5 bps slippage per side of turnover.",
        "",
        "## Baseline (current live parameters)",
        f"- params: `{json.dumps(current)}`",
        f"- train: `{json.dumps(base_train.to_dict() if base_train else None)}`",
        f"- validation: `{json.dumps(base_valid.to_dict() if base_valid else None)}`",
        "",
        "## Candidates (ranked by validation Sharpe)",
    ]
    for i, r in enumerate(rows, 1):
        lines += [
            f"### {i}. `{json.dumps(r['params'])}`",
            f"- hypothesis: {r['hypothesis']}",
            f"- train: `{json.dumps(r['train'])}` | validation: `{json.dumps(r['valid'])}`",
            "",
        ]
    enough_evidence = bool(best and best["valid"]["trading_days"] >= MIN_VALIDATION_DAYS)
    lines += [
        "## Verdict",
        (
            "No candidate beats the current parameters on the validation window. Keep the baseline."
            if not beats_baseline else
            f"Candidate 1 beats the baseline on validation, but the validation window is only "
            f"{best['valid']['trading_days']} trading days (< {MIN_VALIDATION_DAYS}) — too little "
            f"evidence to propose a change. Let history accumulate."
            if not enough_evidence else
            "Candidate 1 beats the baseline on validation with a meaningful evidence window. "
            "A `candidate/<date>` git branch has been created editing only "
            "`engine/driftline/strategy/params.py` — review the diff and this report, run the tests, "
            "merge if convinced, then restart the engine. Nothing applies automatically."
        ),
    ]
    report_path.write_text("\n".join(lines))

    branch = None
    if beats_baseline and enough_evidence:
        branch = write_candidate_branch(best["params"], report_path,
                                        str(datetime.now(timezone.utc).date()))
        if branch:
            log.info("created candidate branch %s — review with: git diff main %s", branch, branch)

    await bus.publish(JournalEntry(
        strategy="strategist", strategy_version=MODEL, kind="proposal",
        text=(
            f"Evaluated {len(rows)} parameter candidates over {days}d of stored bars. "
            + (f"None beat the baseline on validation; keeping current parameters. See {report_path.name}."
               if not beats_baseline else
               f"Best beats baseline on validation (Sharpe {best['valid']['sharpe']:.2f} "
               f"vs {base_valid.sharpe:.2f}) "
               + (f"— branch {branch} awaits human review." if branch else
                  f"but evidence window too small to branch; see {report_path.name}."))
        ),
        payload={"report": str(report_path), "candidates": len(rows), "branch": branch},
    ))
    bus.close()
    log.info("strategist report written: %s", report_path)
    return report_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
