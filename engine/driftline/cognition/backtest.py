"""Deterministic backtester for momentum-rotation parameter candidates.

Pure function of (bars, params) — no LLM anywhere. Simulates the same logic
as BaselineMomentum (rank by lookback return, hold top-N equal weight,
rebalance on a cadence) over stored daily closes, charging slippage on
turnover. Small universe × daily bars: plain Python is plenty fast.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SLIPPAGE_BPS = 5.0  # per side, deliberately pessimistic for daily ETFs


@dataclass
class BacktestResult:
    total_return: float
    max_drawdown: float
    sharpe: float
    annual_turnover: float
    trading_days: int

    def to_dict(self) -> dict:
        return {
            "total_return": round(self.total_return, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe": round(self.sharpe, 2),
            "annual_turnover": round(self.annual_turnover, 1),
            "trading_days": self.trading_days,
        }


def run_backtest(closes: dict[str, list[float]], lookback: int, top_n: int,
                 target_gross: float, rebalance_days: int) -> BacktestResult | None:
    """closes: symbol -> aligned list of daily closes (same length, same dates)."""
    symbols = sorted(closes)
    days = min(len(closes[s]) for s in symbols)
    if days <= lookback + 5:
        return None

    weights = {s: 0.0 for s in symbols}
    equity = 1.0
    curve = [equity]
    peak = 1.0
    max_dd = 0.0
    rets: list[float] = []
    turnover_total = 0.0
    last_rebalance = -10**9

    for t in range(lookback, days - 1):
        if t - last_rebalance >= rebalance_days:
            momentum = {s: closes[s][t] / closes[s][t - lookback] - 1.0 for s in symbols}
            ranked = sorted(symbols, key=lambda s: momentum[s], reverse=True)
            winners = [s for s in ranked[:top_n] if momentum[s] > 0]
            target = {s: (target_gross / top_n if s in winners else 0.0) for s in symbols}
            turnover = sum(abs(target[s] - weights[s]) for s in symbols)
            equity *= 1 - turnover * SLIPPAGE_BPS / 10_000
            turnover_total += turnover
            weights = target
            last_rebalance = t
        day_ret = sum(
            weights[s] * (closes[s][t + 1] / closes[s][t] - 1.0) for s in symbols
        )
        equity *= 1 + day_ret
        rets.append(day_ret)
        curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)

    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / max(n - 1, 1)
    std = math.sqrt(var)
    sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0.0
    return BacktestResult(
        total_return=equity - 1.0,
        max_drawdown=max_dd,
        sharpe=sharpe,
        annual_turnover=turnover_total * 252 / n if n else 0.0,
        trading_days=n,
    )
