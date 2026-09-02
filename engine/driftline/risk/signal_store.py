"""SignalStore: how cognition-plane output enters the execution plane.

Strategies read bounded parameters from here — never raw LLM text. Every
value is clamped at read time regardless of what was written, and a signal
older than `max_age_hours` decays to the neutral default. This file is part
of the risk plane: automated agents do not get write access to it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..ledger.repo import LedgerRepo

RISK_APPETITE_DEFAULT = 1.0
RISK_APPETITE_MIN = 0.3   # cognition can de-risk, but never below 30% of normal
RISK_APPETITE_MAX = 1.0   # ...and never lever up beyond the strategy's own target
TILT_MIN, TILT_MAX = -1.0, 1.0
MAX_AGE_HOURS = 36        # a stale signal is a dead signal


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class SignalStore:
    def __init__(self, repo: LedgerRepo, max_age_hours: int = MAX_AGE_HOURS):
        self.repo = repo
        self.max_age = timedelta(hours=max_age_hours)

    def _fresh(self, kind: str, key: str, max_age: timedelta | None = None) -> dict | None:
        limit = max_age or self.max_age
        for s in self.repo.latest_signals():
            if s["kind"] == kind and s["key"] == key:
                ts = datetime.fromisoformat(s["ts"])
                if ts.tzinfo is None:  # SQLite drops tzinfo; timestamps are stored UTC
                    ts = ts.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - ts
                return s if age <= limit else None
        return None

    def risk_appetite(self) -> float:
        """Gross-exposure multiplier in [RISK_APPETITE_MIN, RISK_APPETITE_MAX]."""
        s = self._fresh("regime", "market")
        if s is None:
            return RISK_APPETITE_DEFAULT
        return _clamp(s["value"], RISK_APPETITE_MIN, RISK_APPETITE_MAX)

    def symbol_tilt(self, symbol: str) -> float:
        """Per-symbol conviction tilt in [-1, 1]; 0 when absent or stale."""
        s = self._fresh("symbol_tilt", symbol)
        if s is None:
            return 0.0
        return _clamp(s["value"], TILT_MIN, TILT_MAX)

    def earnings_event(self, symbol: str, max_age_hours: int = 96) -> dict | None:
        """Fresh earnings-drift signal: {'score': [-1,1], 'ts': iso} or None.

        The tight default freshness window (4 days) is the drift entry window —
        a week-old earnings signal is not an entry, it's history.
        """
        s = self._fresh("earnings", symbol, max_age=timedelta(hours=max_age_hours))
        if s is None:
            return None
        return {"score": _clamp(s["value"], TILT_MIN, TILT_MAX), "ts": s["ts"],
                "confidence": s["confidence"]}
