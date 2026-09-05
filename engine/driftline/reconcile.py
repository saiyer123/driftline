"""Reconciliation loop: our ledger-derived state vs. what the broker reports.

On divergence beyond tolerance, halt trading and say why — never
auto-correct. Divergence means a bug or an out-of-band action (e.g. a manual
trade in the Alpaca UI), and both deserve a human look before more orders go
out.

Runs even while orders are in flight: a pending order explains a bounded
difference (up to its quantity / notional), so the check tolerates exactly
that much per symbol and flags anything beyond it.
"""

from __future__ import annotations

import asyncio
import logging

from .core.bus import EventBus
from .core.events import HaltEvent, Side
from .portfolio.accounting import Portfolio
from .risk.gate import RiskGate

log = logging.getLogger(__name__)

QTY_TOLERANCE = 1e-4
CASH_TOLERANCE_PCT = 0.005  # 0.5% of equity


class Reconciler:
    def __init__(self, bus: EventBus, portfolio: Portfolio, gate: RiskGate, broker):
        self.bus = bus
        self.portfolio = portfolio
        self.gate = gate
        self.broker = broker

    async def run_forever(self, interval_s: int = 300) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self.check_once()
            except Exception:
                log.exception("reconcile pass failed; will retry")

    async def check_once(self) -> list[str]:
        broker_state = await self.broker.account_state()
        # broker-reported equity is live intraday — feeding it to the gate makes
        # the daily-loss halt a real intraday check instead of close-to-close
        # (the engine's snapshot loop announces any resulting halt within 60s)
        self.gate.observe_equity(broker_state["equity"])

        # what pending orders could legitimately explain
        pending_qty: dict[str, float] = {}
        pending_cash = 0.0
        for r in self.gate.pending:
            sign = 1.0 if r.side == Side.BUY else -1.0
            pending_qty[r.symbol] = pending_qty.get(r.symbol, 0.0) + sign * r.qty
            pending_cash += r.notional

        problems: list[str] = []
        ours = {s: p.qty for s, p in self.portfolio.positions.items() if abs(p.qty) > QTY_TOLERANCE}
        theirs = broker_state["positions"]
        for symbol in sorted(set(ours) | set(theirs)):
            a, b = ours.get(symbol, 0.0), theirs.get(symbol, 0.0)
            diff = b - a
            allowed = pending_qty.get(symbol, 0.0)
            if allowed > 0:      # a pending buy may have (partly) filled
                explained = -QTY_TOLERANCE <= diff <= allowed + QTY_TOLERANCE
            elif allowed < 0:    # a pending sell may have (partly) filled
                explained = allowed - QTY_TOLERANCE <= diff <= QTY_TOLERANCE
            else:
                explained = abs(diff) <= QTY_TOLERANCE
            if not explained:
                problems.append(f"{symbol}: ledger qty {a} vs broker qty {b}"
                                + (f" (pending {allowed:+})" if allowed else ""))

        equity = max(broker_state["equity"], 1.0)
        cash_diff = abs(self.portfolio.cash - broker_state["cash"])
        if cash_diff > CASH_TOLERANCE_PCT * equity + pending_cash:
            problems.append(
                f"cash: ledger ${self.portfolio.cash:,.2f} vs broker ${broker_state['cash']:,.2f}"
                + (f" (pending ${pending_cash:,.0f})" if pending_cash else "")
            )

        if problems:
            reason = "reconciliation divergence: " + "; ".join(problems)
            log.error(reason)
            self.gate.halt(reason, kind="reconcile")
            await self.bus.publish(HaltEvent(source="reconcile", reason=reason))
        return problems
