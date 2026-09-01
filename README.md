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
uv run pytest -q                                  # 24 tests
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

## Roadmap

1. ✅ Deterministic skeleton on paper + dashboard v1 (this)
2. Dashboard polish, Grafana-style alerting
3. The reading edge: EDGAR filings + news → Claude regime/name signals (bounded parameters)
4. Overnight strategy-evolution loop; Claude ships strategy changes as PRs
5. Small live capital, per-strategy, gated on forward paper records
