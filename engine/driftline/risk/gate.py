"""The risk gate: deterministic veto layer between strategies and the broker.

Every OrderIntent passes through `check` before it may reach a broker. The
gate holds no opinions about alpha — it enforces limits. Claude agents get no
write access to this module or its config.

Rules enforced:
  * kill switch (flag file or in-memory flag set via API/reconciler)
  * halt state, by kind:
      - "daily_loss": new risk is blocked; position-REDUCING sells are allowed
        so a strategy's protective exit is never trapped behind the halt
        (owner-authorized 2026-09-05)
      - "reconcile" / "infra" / "manual": everything is blocked — when the
        book itself is in doubt, a sell can create risk instead of removing it
  * long-only: sells may only close existing positions, net of sells already
    pending at the broker
  * per-order and per-symbol position size vs. max_position_pct of equity,
    counting buys already pending at the broker
  * gross exposure cap, counting pending buys
  * cash: pending buys reserve cash until they reach a terminal state
  * max daily loss  -> trips a persistent "daily_loss" halt
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

HALT_KINDS = ("daily_loss", "reconcile", "infra", "manual")


@dataclass
class GateDecision:
    allowed: bool
    reason: str = ""


@dataclass
class Reservation:
    intent_id: str
    symbol: str
    side: Side
    qty: float
    notional: float


class RiskGate:
    def __init__(self, settings: Settings, portfolio: Portfolio, kill_switch_path: Path | None = None):
        self.s = settings
        self.portfolio = portfolio
        self.kill_switch_path = kill_switch_path or settings.kill_switch_path
        self._killed = False
        self._halted = False
        self._halt_reason = ""
        self._halt_kind = ""
        self._recent_orders: deque[datetime] = deque(maxlen=1000)
        self._day_start_equity: float | None = None
        self._day: datetime | None = None
        self._pending: dict[str, Reservation] = {}

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

    @property
    def halt_kind(self) -> str:
        return self._halt_kind

    def kill(self, reason: str = "manual kill switch") -> None:
        self._killed = True
        self._halt_reason = reason
        self.kill_switch_path.touch()

    def halt(self, reason: str, kind: str = "manual") -> None:
        if kind not in HALT_KINDS:
            kind = "manual"
        self._halted = True
        self._halt_reason = reason
        self._halt_kind = kind

    def resume(self, reason: str = "manual resume") -> None:
        self._killed = False
        self._halted = False
        self._halt_reason = ""
        self._halt_kind = ""
        self.kill_switch_path.unlink(missing_ok=True)

    # -- pending-order reservations --------------------------------------------

    def reserve(self, intent: OrderIntent, price: float) -> None:
        """Hold cash / exposure / sellable quantity for an order until it is terminal."""
        self._pending[intent.intent_id] = Reservation(
            intent_id=intent.intent_id, symbol=intent.symbol, side=intent.side,
            qty=intent.qty, notional=intent.qty * price,
        )

    def release(self, intent_id: str) -> None:
        self._pending.pop(intent_id, None)

    def consume(self, intent_id: str, filled_qty: float) -> None:
        """A fill shrinks the reservation; a complete fill releases it. Keeps
        the gate honest for partial fills and for brokers that report fills
        before (or without) a terminal order status."""
        r = self._pending.get(intent_id)
        if r is None:
            return
        if filled_qty >= r.qty - 1e-9:
            self._pending.pop(intent_id, None)
            return
        unit = r.notional / r.qty if r.qty else 0.0
        r.qty -= filled_qty
        r.notional = max(r.notional - unit * filled_qty, 0.0)

    @property
    def pending(self) -> list[Reservation]:
        return list(self._pending.values())

    def _pending_buy_notional(self, symbol: str | None = None) -> float:
        return sum(r.notional for r in self._pending.values()
                   if r.side == Side.BUY and (symbol is None or r.symbol == symbol))

    def _pending_sell_qty(self, symbol: str) -> float:
        return sum(r.qty for r in self._pending.values() if r.side == Side.SELL and r.symbol == symbol)

    # -- daily loss tracking -------------------------------------------------

    def reset_day(self, equity: float, now: datetime | None = None) -> None:
        """Start a fresh daily-loss baseline (e.g. when live trading arms)."""
        self._day = now or datetime.now(timezone.utc)
        self._day_start_equity = equity

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
                    f"(limit {self.s.max_daily_loss_pct:.2%})",
                    kind="daily_loss",
                )

    # -- the gate ------------------------------------------------------------

    def check(self, intent: OrderIntent, price: float, now: datetime | None = None) -> GateDecision:
        now = now or datetime.now(timezone.utc)

        if self.killed:
            return GateDecision(False, "kill switch is set")
        if intent.qty <= 0:
            return GateDecision(False, f"non-positive quantity {intent.qty}")
        if price <= 0:
            return GateDecision(False, f"no valid price for {intent.symbol}")

        pos = self.portfolio.positions.get(intent.symbol)
        pos_qty = pos.qty if pos else 0.0
        pending_sells = self._pending_sell_qty(intent.symbol)
        sellable = pos_qty - pending_sells
        reduces_position = intent.side == Side.SELL and intent.qty <= sellable + 1e-9

        if self._halted:
            if self._halt_kind == "daily_loss" and reduces_position:
                pass  # a protective exit must never be trapped behind a loss halt
            else:
                return GateDecision(False, f"engine halted [{self._halt_kind}]: {self._halt_reason}")

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

        if intent.side == Side.SELL:
            if intent.qty > sellable + 1e-9:
                extra = f" minus {pending_sells} already pending" if pending_sells > 0 else ""
                return GateDecision(
                    False, f"long-only: sell {intent.qty} exceeds held {pos_qty} {intent.symbol}{extra}"
                )
        else:
            order_value = intent.qty * price
            pending_sym = self._pending_buy_notional(intent.symbol)
            new_pos_value = pos_qty * price + pending_sym + order_value
            max_pos_value = self.s.max_position_pct * equity
            if new_pos_value > max_pos_value:
                return GateDecision(
                    False,
                    f"position limit: {intent.symbol} would be ${new_pos_value:,.0f} "
                    f"(incl. ${pending_sym:,.0f} pending), limit ${max_pos_value:,.0f} "
                    f"({self.s.max_position_pct:.0%} of equity)",
                )
            pending_all = self._pending_buy_notional()
            new_gross = self.portfolio.gross_exposure + pending_all + order_value
            max_gross = self.s.max_gross_exposure_pct * equity
            if new_gross > max_gross:
                return GateDecision(
                    False,
                    f"gross exposure limit: would be ${new_gross:,.0f} (incl. ${pending_all:,.0f} pending), "
                    f"limit ${max_gross:,.0f} ({self.s.max_gross_exposure_pct:.0%} of equity)",
                )
            available_cash = self.portfolio.cash - pending_all
            if order_value > available_cash:
                return GateDecision(
                    False, f"insufficient cash: order ${order_value:,.0f} > available ${available_cash:,.0f} "
                           f"(cash ${self.portfolio.cash:,.0f} minus ${pending_all:,.0f} reserved)"
                )

        self._recent_orders.append(now)
        return GateDecision(True)
