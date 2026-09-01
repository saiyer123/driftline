# Driftline

Claude-powered swing-trading platform. **Paper trading only in this phase** — the engine
refuses to start unless `ALPACA_PAPER=true`.

## Architecture (three planes)

- `engine/` — deterministic execution plane (Python 3.12, uv). Feed → strategies →
  **risk gate** → broker → event-sourced ledger (SQLite WAL). No LLM calls anywhere
  in this path.
- `dashboard/` — Next.js console talking to the engine's FastAPI on `127.0.0.1:8484`
  (REST + `/ws` event stream).
- `engine/driftline/cognition/` — the cognition plane: a **separate process** running
  Claude jobs (researcher, reviewer, strategist). It writes bounded `ResearchSignal`s
  and journal entries to the ledger; the engine consumes them only through
  `risk/signal_store.py`, which clamps every value and decays stale signals to
  neutral. Design in `docs/driftline-research.html`.

## Iron rules

1. `engine/driftline/risk/gate.py` and `engine/driftline/config.py` risk limits are
   **human-owned**. Automated agents must never edit them; changes require an explicit
   user request.
2. Strategies emit `OrderIntent`s only; nothing but `TradingEngine` may call a broker,
   and only with a gate-approved intent.
3. Never weaken or delete tests to make them pass. The long-only, position-cap, and
   daily-loss tests encode safety invariants.
4. New strategies subclass `strategy/base.py:Strategy`, must journal every decision
   (`JournalEntry`), and are tagged with the git SHA for attribution.
5. Backtests with an LLM in the loop over pre-training-cutoff data are contaminated
   (parametric look-ahead bias) — evaluate forward on paper instead. The strategist
   keeps Claude proposal-only: candidates are scored by the deterministic backtester
   (`cognition/backtest.py`) with a held-out validation window Claude never sees.
6. `risk/signal_store.py` is part of the risk plane (same protection as the gate):
   LLM output enters the trade path only through its clamps. The researcher can
   de-risk (floor 0.3×) but can never lever up (cap 1.0×); stale signals decay to
   neutral. Strategist output is a report — a human edits strategy constants to apply.

## Commands

- Engine tests: `cd engine && uv run pytest -q`
- Replay run (no keys needed): `cd engine && uv run python scripts/make_replay_csv.py && uv run python -m driftline.runner --replay replay-bars.csv`
- Live paper run (needs `.env` from `.env.example`): `cd engine && uv run python -m driftline.runner`
- Dashboard: `cd dashboard && npm run dev` → http://localhost:3000
- Cognition daemon (needs `ANTHROPIC_API_KEY` in `.env` or an `ant auth login` profile):
  `cd engine && uv run python -m driftline.cognition.daemon` — schedules research
  (weekdays 13:00 UTC), review (weekdays 21:30 UTC), strategist (Sat 02:00 UTC).
  One-shot: `... daemon --once research|review|strategist`.

## Conventions

- Events (`engine/driftline/core/events.py`) are the only inter-component contract;
  add new event types there and persist them in `ledger/repo.py`.
- Money is float dollars in phase 1; timestamps are timezone-aware UTC everywhere.
- The ledger is append-only; state is derived, never updated in place.
