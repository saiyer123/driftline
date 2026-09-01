"""The risk gate: deterministic veto layer between strategies and the broker.

Every OrderIntent passes through `check` before it may reach a broker. The
gate holds no opinions about alpha — it enforces limits. In later phases
Claude agents get no write access to this module or its config.

Rules enforced:
  * kill switch (flag file or in-memory flag set via API/reconciler)
  * halt state (daily-loss breach, reconcile divergence)
  * long-only: sells may only close existing positions (phase 1)
  * per-order and per-symbol position size vs. max_position_pct of equity
  * gross exposure cap
  * max daily loss  -> trips a persistent halt
  * order rate limit
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Settings
from ..core.events import OrderIntent, Side
from ..portfolio.accounting import Portfolio


@dataclass
class GateDecision:
    allowed: bool
    reason: str = ""


class RiskGate:
    def __init__(self, settings: Settings, portfolio: Portfolio, kill_switch_path: Path | None = None):
        self.s = settings
        self.portfolio = portfolio
        self.kill_switch_path = kill_switch_path or settings.kill_switch_path
        self._killed = False
        self._halted = False
        self._halt_reason = ""
        self._recent_orders: deque[datetime] = deque(maxlen=1000)
        self._day_start_equity: float | None = None
        self._day: datetime | None = None

    # -- state ---------------------------------------------------------------

    @property
    def killed(self) -> bool:
        return self._killed or self.kill_switch_path.exists()

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def kill(self, reason: str = "manual kill switch") -> None:
        self._killed = True
        self._halt_reason = reason
        self.kill_switch_path.touch()

    def halt(self, reason: str) -> None:
        self._halted = True
        self._halt_reason = reason

    def resume(self, reason: str = "manual resume") -> None:
        self._killed = False
        self._halted = False
        self._halt_reason = ""
        self.kill_switch_path.unlink(missing_ok=True)

    # -- daily loss tracking -------------------------------------------------

    def observe_equity(self, equity: float, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        if self._day is None or now.date() != self._day.date():
            self._day = now
            self._day_start_equity = equity
            return
        if self._day_start_equity and self._day_start_equity > 0:
            dd = (self._day_start_equity - equity) / self._day_start_equity
            if dd >= self.s.max_daily_loss_pct and not self._halted:
                self.halt(
                    f"daily loss limit breached: down {dd:.2%} vs day start "
                    f"(limit {self.s.max_daily_loss_pct:.2%})"
                )

    # -- the gate ------------------------------------------------------------

    def check(self, intent: OrderIntent, price: float, now: datetime | None = None) -> GateDecision:
        now = now or datetime.now(timezone.utc)

        if self.killed:
            return GateDecision(False, "kill switch is set")
        if self._halted:
            return GateDecision(False, f"engine halted: {self._halt_reason}")
        if intent.qty <= 0:
            return GateDecision(False, f"non-positive quantity {intent.qty}")
        if price <= 0:
            return GateDecision(False, f"no valid price for {intent.symbol}")

        cutoff = now - timedelta(minutes=1)
        recent = sum(1 for t in self._recent_orders if t >= cutoff)
        if recent >= self.s.max_orders_per_minute:
            return GateDecision(
                False, f"order rate limit: {recent} orders in the last minute "
                       f"(limit {self.s.max_orders_per_minute})"
            )

        equity = self.portfolio.equity
        if equity <= 0:
            return GateDecision(False, "non-positive account equity")

        pos = self.portfolio.positions.get(intent.symbol)
        pos_qty = pos.qty if pos else 0.0

        if intent.side == Side.SELL:
            if intent.qty > pos_qty + 1e-9:
                return GateDecision(
                    False, f"long-only: sell {intent.qty} exceeds held {pos_qty} {intent.symbol}"
                )
        else:
            order_value = intent.qty * price
            new_pos_value = pos_qty * price + order_value
            max_pos_value = self.s.max_position_pct * equity
            if new_pos_value > max_pos_value:
                return GateDecision(
                    False,
                    f"position limit: {intent.symbol} would be ${new_pos_value:,.0f}, "
                    f"limit ${max_pos_value:,.0f} ({self.s.max_position_pct:.0%} of equity)",
                )
            new_gross = self.portfolio.gross_exposure + order_value
            max_gross = self.s.max_gross_exposure_pct * equity
            if new_gross > max_gross:
                return GateDecision(
                    False,
                    f"gross exposure limit: would be ${new_gross:,.0f}, "
                    f"limit ${max_gross:,.0f} ({self.s.max_gross_exposure_pct:.0%} of equity)",
                )
            if order_value > self.portfolio.cash:
                return GateDecision(
                    False, f"insufficient cash: order ${order_value:,.0f} > cash ${self.portfolio.cash:,.0f}"
                )

        self._recent_orders.append(now)
        return GateDecision(True)
