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
    MarketBar,
    OrderUpdate,
    PositionSnapshot,
    ResearchSignal,
    ResumeEvent,
)
from .models import (
    BarRow,
    Base,
    EquitySnapshotRow,
    FillRow,
    HaltRow,
    JournalRow,
    OrderRow,
    PositionSnapshotRow,
    SignalRow,
)


class LedgerRepo:
    def __init__(self, db_path: Path | str):
        url = f"sqlite:///{db_path}"
        self.engine = create_engine(url, connect_args={"check_same_thread": False})
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
        Base.metadata.create_all(self.engine)
        # migrate pre-constraint databases: drop duplicate bars, then enforce
        # uniqueness so warm-up re-backfills stop re-persisting history
        with self.engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM bars WHERE id NOT IN "
                "(SELECT MIN(id) FROM bars GROUP BY symbol, bar_ts)"
            ))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_bars_symbol_ts ON bars (symbol, bar_ts)"
            ))
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(bars)"))]
            if "feed" not in cols:
                conn.execute(text("ALTER TABLE bars ADD COLUMN feed VARCHAR(8) DEFAULT 'iex'"))

    # -- bus handler ---------------------------------------------------------

    async def on_event(self, event: Event) -> None:
        self.record(event)

    def record(self, event: Event) -> None:
        """Persist one event synchronously (also used for ledger-only writes that
        must not go through the bus, e.g. fills already reflected in a
        broker-seeded portfolio). Raises on write failure — the bus marks this
        handler critical so the engine halts instead of trading blind."""
        from sqlalchemy.exc import IntegrityError
        row = self._row_for(event)
        if row is None:
            return
        with Session(self.engine) as s:
            try:
                s.add(row)
                s.commit()
            except IntegrityError:
                s.rollback()  # duplicate bar from a warm-up re-backfill

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
        if isinstance(e, ResearchSignal):
            return SignalRow(
                ts=e.ts, kind=e.kind, key=e.key, value=e.value,
                confidence=e.confidence, reasoning=e.reasoning,
                source_model=e.source_model,
            )
        if isinstance(e, MarketBar):
            return BarRow(
                bar_ts=e.bar_ts, symbol=e.symbol, open=e.open, high=e.high,
                low=e.low, close=e.close, volume=e.volume, feed=e.feed,
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
        """Per (strategy, strategy_version): realized P&L (average-cost), fees,
        fill count, and the open quantity per symbol so the API can add
        unrealized P&L at current marks. Cash flow alone is not profit."""
        with Session(self.engine) as s:
            rows = s.scalars(select(FillRow).order_by(FillRow.ts)).all()
        agg: dict[tuple[str, str], dict] = {}
        for r in rows:
            key = (r.strategy, r.strategy_version)
            a = agg.setdefault(key, {"strategy": r.strategy, "strategy_version": r.strategy_version,
                                     "realized": 0.0, "fees": 0.0, "fills": 0, "cashflow": 0.0,
                                     "open": {}})  # symbol -> {"qty", "avg"}
            pos = a["open"].setdefault(r.symbol, {"qty": 0.0, "avg": 0.0})
            if r.side == "buy":
                total = pos["avg"] * pos["qty"] + r.price * r.qty
                pos["qty"] += r.qty
                pos["avg"] = total / pos["qty"] if pos["qty"] else 0.0
                a["cashflow"] -= r.price * r.qty
            else:
                sold = min(r.qty, pos["qty"]) if pos["qty"] > 0 else r.qty
                a["realized"] += (r.price - pos["avg"]) * sold
                pos["qty"] = max(pos["qty"] - r.qty, 0.0)
                if pos["qty"] <= 1e-9:
                    pos["qty"], pos["avg"] = 0.0, 0.0
                a["cashflow"] += r.price * r.qty
            a["realized"] -= r.fee
            a["fees"] += r.fee
            a["fills"] += 1
        out = []
        for a in agg.values():
            a["open"] = {sym: p for sym, p in a["open"].items() if p["qty"] > 1e-9}
            out.append(a)
        return sorted(out, key=lambda a: a["realized"], reverse=True)

    def latest_signals(self) -> list[dict]:
        """Most recent signal per (kind, key)."""
        with Session(self.engine) as s:
            rows = s.scalars(select(SignalRow).order_by(SignalRow.ts.desc()).limit(500)).all()
        seen: set[tuple[str, str]] = set()
        out = []
        for r in rows:
            k = (r.kind, r.key)
            if k in seen:
                continue
            seen.add(k)
            out.append({
                "ts": r.ts.isoformat(), "kind": r.kind, "key": r.key,
                "value": r.value, "confidence": r.confidence,
                "reasoning": r.reasoning, "source_model": r.source_model,
            })
        return out

    def daily_bars(self, symbol: str, limit: int = 400) -> list[dict]:
        """Stored daily bars, oldest first, deduped to one bar per day."""
        with Session(self.engine) as s:
            rows = s.scalars(
                select(BarRow).where(BarRow.symbol == symbol)
                .order_by(BarRow.bar_ts.desc()).limit(limit * 2)
            ).all()
        seen_days: set[str] = set()
        out = []
        for r in rows:
            day = r.bar_ts.date().isoformat()
            if day in seen_days:
                continue
            seen_days.add(day)
            out.append({"date": day, "open": r.open, "high": r.high, "low": r.low,
                        "close": r.close, "volume": r.volume})
            if len(out) >= limit:
                break
        return list(reversed(out))

    def filing_seen(self, accession: str) -> bool:
        from .models import FilingRow
        with Session(self.engine) as s:
            return s.scalar(select(FilingRow.id).where(FilingRow.accession == accession)) is not None

    def mark_filing(self, symbol: str, form: str, filed: str, accession: str) -> None:
        from .models import FilingRow
        with Session(self.engine) as s:
            s.add(FilingRow(ts=datetime.now(timezone.utc), symbol=symbol,
                            form=form, filed=filed, accession=accession))
            s.commit()

    def strategy_positions(self, strategy: str) -> dict[str, dict]:
        """Net qty and last-entry timestamp per symbol from this strategy's fills.

        Lets a restarted strategy rebuild its holding state deterministically.
        """
        with Session(self.engine) as s:
            rows = s.scalars(
                select(FillRow).where(FillRow.strategy == strategy).order_by(FillRow.ts)
            ).all()
        out: dict[str, dict] = {}
        for r in rows:
            p = out.setdefault(r.symbol, {"qty": 0.0, "last_entry": None})
            if r.side == "buy":
                p["qty"] += r.qty
                p["last_entry"] = r.ts.isoformat()
            else:
                p["qty"] -= r.qty
        return {sym: p for sym, p in out.items() if p["qty"] > 1e-9}

    def intent_strategy(self, intent_id: str) -> tuple[str, str] | None:
        """(strategy, strategy_version) that submitted an intent, from orders."""
        with Session(self.engine) as s:
            row = s.scalars(
                select(OrderRow).where(OrderRow.intent_id == intent_id)
                .order_by(OrderRow.ts).limit(1)
            ).first()
        return (row.strategy, row.strategy_version) if row else None

    def non_terminal_intents(self, limit: int = 200) -> list[dict]:
        """Intents whose latest order row is approved/submitted/accepted — orders
        the broker may have finished while the engine was down."""
        with Session(self.engine) as s:
            rows = s.scalars(select(OrderRow).order_by(OrderRow.id)).all()  # insertion order, not ts:
        latest: dict[str, OrderRow] = {}                                     # intents carry bar time,
        for r in rows:                                                       # updates carry wall time
            latest[r.intent_id] = r
        open_states = {"approved", "submitted", "accepted", "partially_filled"}
        out = [{"intent_id": r.intent_id, "symbol": r.symbol, "side": r.side, "qty": r.qty,
                "strategy": r.strategy, "strategy_version": r.strategy_version, "ts": r.ts.isoformat()}
               for r in latest.values() if r.status in open_states]
        return out[-limit:]

    def ledger_positions(self) -> dict[str, float]:
        """Net quantity per symbol implied by every recorded fill."""
        with Session(self.engine) as s:
            rows = s.scalars(select(FillRow)).all()
        qty: dict[str, float] = {}
        for r in rows:
            qty[r.symbol] = qty.get(r.symbol, 0.0) + (r.qty if r.side == "buy" else -r.qty)
        return {s_: q for s_, q in qty.items() if abs(q) > 1e-9}

    def last_decision(self, strategy: str) -> dict | None:
        """Most recent decision journal entry for a strategy (ts, text, payload)."""
        with Session(self.engine) as s:
            row = s.scalars(
                select(JournalRow)
                .where(JournalRow.strategy == strategy, JournalRow.kind == "decision")
                .order_by(JournalRow.ts.desc()).limit(1)
            ).first()
        if row is None:
            return None
        return {"ts": row.ts.isoformat(), "text": row.text, "payload": row.payload}

    def open_halt(self) -> dict | None:
        """The latest halt if it has no later resume — a restart must honor it."""
        rows = self.halts(limit=1)
        if rows and rows[0]["action"] == "halt":
            return rows[0]
        return None

    def day_start_equity(self, day_iso: str) -> float | None:
        """First equity snapshot of the given UTC day — the daily-loss baseline.

        Restarting mid-day must not reset the loss budget to current equity.
        """
        with Session(self.engine) as s:
            rows = s.scalars(
                select(EquitySnapshotRow).order_by(EquitySnapshotRow.ts)
            ).all()
        for r in rows:
            if r.ts.date().isoformat() == day_iso:
                return r.equity
        return None

    def halts(self, limit: int = 50) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.scalars(select(HaltRow).order_by(HaltRow.ts.desc()).limit(limit)).all()
        return [
            {"ts": r.ts.isoformat(), "action": r.action, "source": r.source, "reason": r.reason}
            for r in rows
        ]
