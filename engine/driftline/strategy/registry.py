"""Strategy registry: select strategies by name (runner `--strategies a,b`).

The one engine file that differs per platform. Each entry is (factory,
symbols the strategy needs from the feed). This platform (the original
Driftline) trades the ETF momentum baseline plus the earnings-drift module.
"""

from __future__ import annotations

from typing import Callable

from ..portfolio.accounting import Portfolio
from .base import Strategy
from .baseline_momentum import UNIVERSE, BaselineMomentum
from .earnings_drift import EarningsDrift
from .watchlist import WATCHLIST

Factory = Callable[[Portfolio, object, object], Strategy]

REGISTRY: dict[str, tuple[Factory, list[str]]] = {
    "baseline_momentum": (lambda p, signals, repo: BaselineMomentum(p, signals=signals, repo=repo), UNIVERSE),
    "earnings_drift": (lambda p, signals, repo: EarningsDrift(p, signals=signals, repo=repo), WATCHLIST),
}

DEFAULT_LIVE = "baseline_momentum,earnings_drift"
DEFAULT_REPLAY = "baseline_momentum"


def parse_names(spec: str) -> list[str]:
    names = [n.strip() for n in spec.split(",") if n.strip()]
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise SystemExit(f"unknown strategy name(s): {unknown}; known: {sorted(REGISTRY)}")
    if not names:
        raise SystemExit("at least one strategy name is required")
    return names


def build_strategies(names: list[str], portfolio: Portfolio,
                     signals=None, repo=None) -> list[Strategy]:
    return [REGISTRY[n][0](portfolio, signals, repo) for n in names]


def symbols_for(names: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for n in names:
        for s in REGISTRY[n][1]:
            seen.setdefault(s, None)
    return list(seen)
