---
name: driftline-strategy-review
description: Mandatory review checklist for any Driftline strategy change — new strategies, parameter promotions from strategist reports, or edits to existing strategy code. Run before merging or promoting anything that influences trading.
---

# Driftline strategy review

Run every check. A single failure blocks the change.

## Safety invariants

1. **Long-only preserved**: no code path can produce a net short (sells only close held positions). The gate enforces it, but the strategy must not rely on vetoes as control flow.
2. **No broker access**: strategy emits `OrderIntent` only; grep the diff for any import of `broker/` or `alpaca` — must be none.
3. **No edits to the risk plane**: `risk/gate.py`, `risk/signal_store.py`, and risk limits in `config.py` untouched (unless the user explicitly requested a limit change themselves).
4. **LLM output bounded**: any new signal consumed from the cognition plane goes through `SignalStore` (or an equivalently clamped, staleness-decayed reader added to `risk/`), never raw values or free text.
5. **Tests intact and green**: no safety test weakened, deleted, or skipped. New behavior has new tests. `uv run pytest -q` passes.

## Quality checks

6. **Journals every decision**: a `JournalEntry` with real reasoning per decision cycle, stamped with bar time (not wall clock).
7. **Bar-time stamping**: all emitted events use `ts=bar_ts` so replay == live (the warm-up rate-limit bug class).
8. **Warm-up safe**: strategy state builds from backfill bars without trading; `on_go_live()` resets cadence correctly.
9. **Sells before buys** in rebalance output so cash frees up before the gate's cash check.
10. **Cost-aware**: expected turnover estimated; backtest evidence includes 5 bps/side slippage minimum. Reject changes justified only by a frictionless backtest.

## Evidence standards (for promotions)

11. **Walk-forward only**: candidate beat the baseline on the *validation* window (never shown to Claude), not just training.
12. **Enough data**: windows under ~6 months of trading days are weak evidence — say so explicitly and prefer waiting.
13. **No LLM-in-the-loop backtests over pre-cutoff data** (parametric look-ahead bias) — the researcher signal is only validated forward on paper.
14. **Paper soak before live**: any promoted change runs on paper and is compared against the prior version via `/attribution` (per-git-SHA P&L) before any talk of real capital.

## Output

Report pass/fail per item with file:line evidence, then an overall verdict. If promoting a strategist candidate, restate its train AND validation metrics and the size of the evidence window.
