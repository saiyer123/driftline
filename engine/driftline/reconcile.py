"""Reconciliation loop: our ledger-derived state vs. what the broker reports.

On divergence beyond tolerance, halt trading and say why — never
auto-correct. Divergence means a bug or an out-of-band action (e.g. a manual
trade in the Alpaca UI), and both deserve a human look before more orders go
out.
"""

from __future__ import annotations

import asyncio
import logging

from .core.bus import EventBus
from .core.events import HaltEvent
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
        if getattr(self.broker, "has_open_orders", False):
            log.info("reconcile skipped: orders in flight (fills may not be ingested yet)")
            return []
        broker_state = await self.broker.account_state()
        problems: list[str] = []

        ours = {s: p.qty for s, p in self.portfolio.positions.items() if abs(p.qty) > QTY_TOLERANCE}
        theirs = broker_state["positions"]
        for symbol in sorted(set(ours) | set(theirs)):
            a, b = ours.get(symbol, 0.0), theirs.get(symbol, 0.0)
            if abs(a - b) > QTY_TOLERANCE:
                problems.append(f"{symbol}: ledger qty {a} vs broker qty {b}")

        equity = max(broker_state["equity"], 1.0)
        if abs(self.portfolio.cash - broker_state["cash"]) > CASH_TOLERANCE_PCT * equity:
            problems.append(
                f"cash: ledger ${self.portfolio.cash:,.2f} vs broker ${broker_state['cash']:,.2f}"
            )

        if problems:
            reason = "reconciliation divergence: " + "; ".join(problems)
            log.error(reason)
            self.gate.halt(reason)
            await self.bus.publish(HaltEvent(source="reconcile", reason=reason))
        return problems
