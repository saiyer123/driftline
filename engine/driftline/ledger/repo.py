"""Ledger repository: the only component that writes to the database.

Subscribes to the bus and persists every relevant event; also serves the
read queries the API needs. Callers never see SQLAlchemy sessions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from ..core.events import (
    EquitySnapshot,
    Event,
    Fill,
    HaltEvent,
    JournalEntry,
    OrderUpdate,
    PositionSnapshot,
    ResumeEvent,
)
from .models import (
    Base,
    EquitySnapshotRow,
    FillRow,
    HaltRow,
    JournalRow,
    OrderRow,
    PositionSnapshotRow,
)


class LedgerRepo:
    def __init__(self, db_path: Path | str):
        url = f"sqlite:///{db_path}"
        self.engine = create_engine(url, connect_args={"check_same_thread": False})
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
        Base.metadata.create_all(self.engine)

    # -- bus handler ---------------------------------------------------------

    async def on_event(self, event: Event) -> None:
        with Session(self.engine) as s:
            row = self._row_for(event)
            if row is not None:
                s.add(row)
                s.commit()

    def _row_for(self, e: Event):
        if isinstance(e, OrderUpdate):
            return OrderRow(
                ts=e.ts, intent_id=e.intent_id, broker_order_id=e.broker_order_id,
                symbol=e.symbol, side=e.side.value, qty=e.qty, status=e.status.value,
                strategy=e.strategy, strategy_version=e.strategy_version, reason=e.reason,
            )
        if isinstance(e, Fill):
            return FillRow(
                ts=e.ts, intent_id=e.intent_id, broker_order_id=e.broker_order_id,
                symbol=e.symbol, side=e.side.value, qty=e.qty, price=e.price,
                fee=e.fee, strategy=e.strategy, strategy_version=e.strategy_version,
            )
        if isinstance(e, PositionSnapshot):
            return PositionSnapshotRow(
                ts=e.ts, symbol=e.symbol, qty=e.qty, avg_entry=e.avg_entry,
                mark=e.mark, unrealized_pnl=e.unrealized_pnl,
            )
        if isinstance(e, EquitySnapshot):
            return EquitySnapshotRow(
                ts=e.ts, equity=e.equity, cash=e.cash, gross_exposure=e.gross_exposure,
                realized_pnl_today=e.realized_pnl_today, unrealized_pnl=e.unrealized_pnl,
            )
        if isinstance(e, JournalEntry):
            return JournalRow(
                ts=e.ts, strategy=e.strategy, strategy_version=e.strategy_version,
                kind=e.kind, text=e.text, payload=e.payload,
            )
        if isinstance(e, HaltEvent):
            return HaltRow(ts=e.ts, action="halt", source=e.source, reason=e.reason)
        if isinstance(e, ResumeEvent):
            return HaltRow(ts=e.ts, action="resume", source=e.source, reason=e.reason)
        return None

    # -- queries (API layer) -------------------------------------------------

    def equity_curve(self, since_hours: int = 24 * 30) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        with Session(self.engine) as s:
            rows = s.scalars(
                select(EquitySnapshotRow)
                .where(EquitySnapshotRow.ts >= cutoff)
                .order_by(EquitySnapshotRow.ts)
            ).all()
        return [
            {
                "ts": r.ts.isoformat(), "equity": r.equity, "cash": r.cash,
                "gross_exposure": r.gross_exposure,
                "realized_pnl_today": r.realized_pnl_today,
                "unrealized_pnl": r.unrealized_pnl,
            }
            for r in rows
        ]

    def latest_positions(self) -> list[dict]:
        with Session(self.engine) as s:
            latest_ts = s.scalar(
                select(PositionSnapshotRow.ts).order_by(PositionSnapshotRow.ts.desc()).limit(1)
            )
            if latest_ts is None:
                return []
            rows = s.scalars(
                select(PositionSnapshotRow).where(PositionSnapshotRow.ts == latest_ts)
            ).all()
        return [
            {
                "ts": r.ts.isoformat(), "symbol": r.symbol, "qty": r.qty,
                "avg_entry": r.avg_entry, "mark": r.mark, "unrealized_pnl": r.unrealized_pnl,
            }
            for r in rows if r.qty != 0
        ]

    def orders(self, limit: int = 200) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.scalars(
                select(OrderRow).order_by(OrderRow.ts.desc()).limit(limit)
            ).all()
        return [
            {
                "ts": r.ts.isoformat(), "intent_id": r.intent_id,
                "broker_order_id": r.broker_order_id, "symbol": r.symbol,
                "side": r.side, "qty": r.qty, "status": r.status,
                "strategy": r.strategy, "strategy_version": r.strategy_version,
                "reason": r.reason,
            }
            for r in rows
        ]

    def fills(self, limit: int = 200) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.scalars(
                select(FillRow).order_by(FillRow.ts.desc()).limit(limit)
            ).all()
        return [
            {
                "ts": r.ts.isoformat(), "intent_id": r.intent_id, "symbol": r.symbol,
                "side": r.side, "qty": r.qty, "price": r.price, "fee": r.fee,
                "strategy": r.strategy, "strategy_version": r.strategy_version,
            }
            for r in rows
        ]

    def journal(self, limit: int = 200) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.scalars(
                select(JournalRow).order_by(JournalRow.ts.desc()).limit(limit)
            ).all()
        return [
            {
                "ts": r.ts.isoformat(), "strategy": r.strategy,
                "strategy_version": r.strategy_version, "kind": r.kind,
                "text": r.text, "payload": r.payload,
            }
            for r in rows
        ]

    def attribution(self) -> list[dict]:
        """Realized cashflow per (strategy, strategy_version): sells minus buys minus fees.

        For open positions this is cashflow, not final P&L — the dashboard pairs
        it with live unrealized P&L from positions.
        """
        with Session(self.engine) as s:
            rows = s.scalars(select(FillRow)).all()
        agg: dict[tuple[str, str], dict] = {}
        for r in rows:
            key = (r.strategy, r.strategy_version)
            a = agg.setdefault(key, {"strategy": r.strategy, "strategy_version": r.strategy_version,
                                     "cashflow": 0.0, "fees": 0.0, "fills": 0})
            signed = r.qty * r.price * (1 if r.side == "sell" else -1)
            a["cashflow"] += signed - r.fee
            a["fees"] += r.fee
            a["fills"] += 1
        return sorted(agg.values(), key=lambda a: a["cashflow"], reverse=True)

    def halts(self, limit: int = 50) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.scalars(select(HaltRow).order_by(HaltRow.ts.desc()).limit(limit)).all()
        return [
            {"ts": r.ts.isoformat(), "action": r.action, "source": r.source, "reason": r.reason}
            for r in rows
        ]
