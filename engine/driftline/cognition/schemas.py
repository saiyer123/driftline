"""Structured output schemas for the cognition plane.

Every Claude call in Driftline returns one of these validated models —
free-text never crosses into the execution plane. Values are bounded here
AND re-clamped by the engine's SignalStore (defense in depth).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SymbolAssessment(BaseModel):
    symbol: str
    tilt: float = Field(ge=-1.0, le=1.0, description="Conviction tilt: -1 strong avoid, 0 neutral, +1 strong favor")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(description="One or two sentences citing the specific news items")


class ResearchReport(BaseModel):
    regime: str = Field(description="One of: risk_on, neutral, risk_off")
    risk_appetite: float = Field(
        ge=0.3, le=1.0,
        description="Gross exposure multiplier: 1.0 = fully invested per strategy targets, 0.3 = maximum de-risking",
    )
    regime_confidence: float = Field(ge=0.0, le=1.0)
    regime_reasoning: str = Field(description="Two or three sentences on the market backdrop, citing the news reviewed")
    symbols: list[SymbolAssessment] = Field(description="Assessment for each requested symbol")
    notable_risks: list[str] = Field(description="Concrete near-term risks worth monitoring (0-5 items)")


class DailyReview(BaseModel):
    summary: str = Field(description="Plain-language post-mortem of the trading day (3-6 sentences)")
    what_worked: list[str]
    what_to_watch: list[str] = Field(description="Concerns or anomalies in fills, vetoes, halts, or P&L")
    discipline_check: str = Field(description="Did the system follow its rules today? Note any vetoes/halts and whether they were correct")


class ParameterCandidate(BaseModel):
    lookback: int = Field(ge=20, le=252, description="Momentum lookback in trading days")
    top_n: int = Field(ge=1, le=6, description="Number of ETFs held")
    target_gross: float = Field(ge=0.3, le=1.0, description="Target gross exposure fraction")
    rebalance_days: int = Field(ge=5, le=30, description="Minimum days between rebalances")
    hypothesis: str = Field(description="Why this parameterization might beat the current one")


class StrategyProposals(BaseModel):
    market_read: str = Field(description="Brief read of what the recent stored data shows")
    candidates: list[ParameterCandidate] = Field(description="4-8 distinct parameter candidates to backtest")
