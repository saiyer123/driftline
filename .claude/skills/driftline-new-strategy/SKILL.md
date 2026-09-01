---
name: driftline-new-strategy
description: Scaffold and wire a new trading strategy into Driftline following the project's conventions — subclassing, journaling, signal consumption, replay validation, and registration. Use when adding any new strategy or signal-driven trading logic.
---

# Adding a strategy to Driftline

## Structure

1. New file `engine/driftline/strategy/<name>.py`, subclassing `strategy/base.py:Strategy`:
   - `name` class attr (snake_case, stable — it keys P&L attribution).
   - `on_bar(bar) -> list[Event]` returns `JournalEntry` + `OrderIntent`s; never mutates the portfolio; never touches a broker.
   - `on_go_live()` resets cadence state so the first fresh bar can act after warm-up.
   - Stamp every emitted event with `ts=bar.bar_ts` (bar time, not wall clock — replay/live parity).
   - Emit sells before buys.
   - Optional `signals` param (a `SignalStore`) for bounded cognition inputs — read via its clamped methods only.
2. Register it in `runner.py`'s strategy list (both live and replay paths if it should backtest).
3. If it needs a new event or persisted data, add the event in `core/events.py` and persist it in `ledger/repo.py` — events are the only inter-component contract.

## Tests (required, in `engine/tests/`)

- Warm-up: unarmed engine + your strategy over synthetic bars → zero trades.
- Behavior: the strategy trades correctly on bars engineered to trigger it, and stays flat when it shouldn't trade.
- Long-only: it never emits a sell exceeding held quantity.
- Reuse `tests/test_replay_end_to_end.py:synthetic_bars` or build targeted bar sequences.

## Validation before it trades paper

1. `uv run pytest -q` green.
2. Replay it: `uv run python -m driftline.runner --replay replay-bars.csv` — check fills, journal reasoning, and equity curve in the dashboard.
3. Run `/driftline-strategy-review` on the diff.
4. Commit with the strategy described in the message; its git SHA becomes its attribution version.
5. It trades **paper only**, alongside (not replacing) existing strategies, until its forward record justifies more.

## Conventions

- Universe/parameters as module constants (the strategist mutates parameters via reports, humans apply them).
- Journal text must let a reader reconstruct *why* from the entry alone (include the ranked inputs, not just the conclusion).
- Money is float dollars, timestamps timezone-aware UTC.
