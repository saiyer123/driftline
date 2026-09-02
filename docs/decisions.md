# Design decisions

Short records of choices that shape the system, so future sessions (human or
Claude) don't relitigate them without new evidence.

## 2026-09-02 — Off-cycle de-risk: yes, sells-only

A sharp drop in the researcher's risk appetite (≥0.25 below the level used at
the last rebalance AND below 0.6) triggers an immediate sell-down to the new
weights instead of waiting for Monday. Appetite *rises* always wait for the
next rebalance. Rationale: the daily signal's only real-time value is downside
protection; the asymmetry (fast to de-risk, slow to re-risk) matches the
platform's core safety posture and cannot violate long-only.

## 2026-09-02 — Asset-class buckets: not now

Top-3 momentum often holds three correlated US-equity flavors. Accepted for
the baseline: it is the benchmark and must stay stable while the forward
record accumulates; the absolute-momentum filter provides bear protection.
Bucket-picking is a legitimate future *strategist candidate*, to be judged by
the walk-forward backtester like any other proposal — not an ad-hoc edit.

## 2026-09-02 — Symbol tilts: display-only for now

The researcher's per-ETF tilts appear on the dashboard but no strategy
consumes them. Deliberate: wiring them into the ranking mid-experiment would
change the benchmark. Revisit as a strategist candidate.

## 2026-09-02 — Bars carry a `feed` column

Free Alpaca data is IEX-only; closes can differ from consolidated (SIP) tape
by cents. Every stored bar records its provenance ("iex", "sip", "replay") so
future backtests never silently mix series.

## 2026-09-02 — Money stays float until the phase-5 gate

Exact-decimal money (and tightening the reconciler's 0.5% cash tolerance) is
required BEFORE any real capital, not before. Add to the promotion checklist:
fills, cash, and accounting move to Decimal; reconciler tolerance drops to
cents.

## 2026-09-01 — Paper-only, promotion gates, human-owned risk plane

See CLAUDE.md iron rules and docs/driftline-research.html; recorded here for
completeness.
