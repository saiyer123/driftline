# Driftline

An ambitious, safety-railed, Claude-powered swing-trading platform. Phase 1: a
deterministic paper-trading engine (Alpaca) with a live P&L dashboard.

**Research & architecture:** [docs/driftline-research.html](docs/driftline-research.html)
— why swing trading, why "Claude the engineer, not Claude the gambler," and the
three-plane design this repo implements.

## Quick start (no broker account needed)

```bash
cd engine
uv sync
uv run pytest -q
uv run python scripts/make_replay_csv.py          # synthetic daily bars
uv run python -m driftline.runner --replay replay-bars.csv
```

Then in another terminal:

```bash
cd dashboard && npm install && npm run dev
```

Open http://localhost:3000 — equity curve, positions, orders/fills, attribution,
decision journal, and the kill switch, fed by the replay run.

## Live paper trading

1. Create a free Alpaca account and grab **paper** API keys (app.alpaca.markets).
2. `cp .env.example .env` and fill in the keys yourself. Never commit `.env`.
3. `cd engine && uv run python -m driftline.runner`

The engine backfills daily bars, seeds state from the broker, trades the baseline
momentum rotation at most weekly, reconciles against the broker every 5 minutes
(halting on any divergence), and refuses to start if `ALPACA_PAPER` is not true.

## Safety model

- **Risk gate** (`engine/driftline/risk/gate.py`): long-only, per-position and gross
  exposure caps, 3% daily-loss halt, order rate limit, kill switch (dashboard button
  or `KILL` file at the repo root). Strategies cannot bypass it.
- **Event-sourced ledger** (SQLite): every order, fill, snapshot, decision, and halt,
  attributed to a strategy + git SHA.
- **Paper only** in this phase. Real capital comes (much) later, behind forward
  paper results — see the roadmap in the research report.

## The cognition plane (Claude)

A separate process from the engine — the trade path never makes an LLM call.
Needs `ANTHROPIC_API_KEY` in `.env` (console.anthropic.com) or an `ant auth login` profile:

```bash
cd engine && uv run python -m driftline.cognition.daemon
```

- **Researcher** (weekdays 13:00 UTC): reads recent market news (Alpaca news API) +
  stored price action, publishes a bounded regime signal (risk appetite, clamped to
  [0.3, 1.0] — it can de-risk, never lever up) and per-ETF tilts. Shown on the
  dashboard's Signals page; consumed by the strategy at its next rebalance.
- **Reviewer** (weekdays 21:30 UTC): reads the day's ledger and writes an honest
  post-mortem into the Journal (process over outcome).
- **Strategist** (Saturdays 02:00 UTC): Claude proposes parameter candidates; the
  deterministic backtester scores them walk-forward on stored real bars with a
  validation window Claude never sees; a ranked report lands in `candidates/`.
  Promotion is always a human code edit — nothing applies automatically.

One-shot runs: `uv run python -m driftline.cognition.daemon --once research` (or `review`, `strategist`).

## Roadmap

1. ✅ Deterministic skeleton on paper + dashboard v1
2. ✅ Cognition plane: researcher / reviewer / strategist + Signals page
3. Deeper reading edge: EDGAR filings ingestion + pgvector, earnings-drift module
4. Strategy candidates as git branches/PRs; richer evolution loop
5. Small live capital, per-strategy, gated on months of forward paper record
