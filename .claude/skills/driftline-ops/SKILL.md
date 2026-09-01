---
name: driftline-ops
description: Operate the Driftline trading platform — start/stop the engine, dashboard, and cognition daemon, read the ledger, interpret halts and reconciliation breaks, run replay, and diagnose "why is it down today". Use for any operational question about the running system.
---

# Driftline operations

Three processes, all from repo root `~/driftline`:

| Process | Command | Notes |
|---|---|---|
| Engine (live paper) | `cd engine && uv run python -m driftline.runner` | Needs Alpaca keys in root `.env`; refuses to start unless `ALPACA_PAPER=true`. Backfills ~130d of bars (warm-up, unarmed), then logs `engine armed`. API on `127.0.0.1:8484`. |
| Dashboard | `cd dashboard && npm run dev` | http://localhost:3000 |
| Cognition daemon | `cd engine && uv run python -m driftline.cognition.daemon` | Needs `ANTHROPIC_API_KEY` in `.env`. One-shots: `--once research\|review\|strategist`. |

## Reading state

- `curl -s 127.0.0.1:8484/status` — equity, halted flag + reason, positions count.
- Other endpoints: `/equity`, `/positions`, `/orders`, `/fills`, `/journal`, `/signals`, `/attribution`, `/halts`.
- Ledger is SQLite at repo root `driftline.db` (WAL); event log `events.jsonl`. Query via `LedgerRepo` (`engine/driftline/ledger/repo.py`) — never write to the DB by hand.
- "Why is it down today": check `/equity` for the curve, `/fills` for what traded, `/journal` for the strategy's own reasoning, `/signals` for the current regime.

## Halts

A HALTED status means one of:
- **Daily-loss limit** (3% intraday drawdown) — risk gate tripped it. Investigate before resuming.
- **Reconciliation divergence** — ledger vs broker mismatch (often a manual trade in the Alpaca UI, or a missed fill). Compare `/positions` against the Alpaca paper dashboard. Never auto-correct; fix the cause.
- **Manual kill** — dashboard button or `KILL` file at repo root.

Resume only after understanding the cause: dashboard RESUME button or `curl -X POST 127.0.0.1:8484/resume`. Kill: `POST /kill` or `touch ~/driftline/KILL`.

## Common tasks

- Tests: `cd engine && uv run pytest -q` (must stay green; never weaken safety tests).
- Replay without keys: `cd engine && uv run python scripts/make_replay_csv.py && uv run python -m driftline.runner --replay replay-bars.csv`.
- Cancel all open paper orders (cleanup): `uv run python scripts/cancel_open_orders.py`.
- After editing engine code: restart the engine terminal (Ctrl+C, rerun) — it re-adopts open orders and re-seeds state from the broker.
- Strategist reports land in `candidates/report-<date>.md`; promoting one = human edits `engine/driftline/strategy/baseline_momentum.py` constants + tests + paper soak.

## Iron rules (never violate while operating)

Risk limits in `engine/driftline/risk/gate.py`, `risk/signal_store.py`, and `config.py` are human-owned. The engine never calls an LLM. Live mode stays paper-only in this phase. See `CLAUDE.md` for the full list.
