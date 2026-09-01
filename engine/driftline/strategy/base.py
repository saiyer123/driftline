"""Strategy interface.

A strategy consumes MarketBars and returns OrderIntents (plus JournalEntries
explaining itself). It reads portfolio state but never mutates it, and it
never talks to a broker — everything it wants must survive the risk gate.

`strategy_version` is the git SHA of the code that produced the intent, so
P&L attributes to specific strategy iterations (in later phases: to specific
Claude-authored PRs).
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from functools import lru_cache

from ..core.events import Event, MarketBar
from ..portfolio.accounting import Portfolio


@lru_cache(maxsize=1)
def current_git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        sha = out.stdout.strip()
        return sha if out.returncode == 0 and sha else "dev"
    except Exception:
        return "dev"


class Strategy(ABC):
    name: str = "unnamed"

    def __init__(self, portfolio: Portfolio):
        self.portfolio = portfolio
        self.version = current_git_sha()

    @abstractmethod
    def on_bar(self, bar: MarketBar) -> list[Event]:
        """React to a market bar; return OrderIntents / JournalEntries to publish."""

    def on_go_live(self) -> None:
        """Called when warm-up ends and trading arms; reset cadence state so the
        strategy may act on the next fresh bar instead of waiting out a
        rebalance interval that elapsed inside historical data."""
