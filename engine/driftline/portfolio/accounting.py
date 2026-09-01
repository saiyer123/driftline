"""Portfolio accounting: positions, cash, realized/unrealized P&L.

Derives state purely from Fill and MarketBar events. Average-cost method;
long-only in phase 1 (the risk gate enforces that separately).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from ..core.events import (
    EquitySnapshot,
    Fill,
    MarketBar,
    PositionSnapshot,
    Side,
)


@dataclass
class Position:
    symbol: str
    qty: float = 0.0
    avg_entry: float = 0.0
    mark: float = 0.0

    @property
    def unrealized_pnl(self) -> float:
        return (self.mark - self.avg_entry) * self.qty

    @property
    def market_value(self) -> float:
        return self.mark * self.qty


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl_today: float = 0.0
    _pnl_day: date = field(default_factory=lambda: datetime.now(timezone.utc).date())

    def _position(self, symbol: str) -> Position:
        return self.positions.setdefault(symbol, Position(symbol=symbol))

    def _roll_day(self, ts: datetime) -> None:
        if ts.date() != self._pnl_day:
            self._pnl_day = ts.date()
            self.realized_pnl_today = 0.0

    def apply_fill(self, fill: Fill) -> None:
        self._roll_day(fill.ts)
        pos = self._position(fill.symbol)
        if fill.side == Side.BUY:
            total_cost = pos.avg_entry * pos.qty + fill.price * fill.qty
            pos.qty += fill.qty
            pos.avg_entry = total_cost / pos.qty if pos.qty else 0.0
            self.cash -= fill.price * fill.qty + fill.fee
        else:
            realized = (fill.price - pos.avg_entry) * fill.qty - fill.fee
            self.realized_pnl_today += realized
            pos.qty -= fill.qty
            if pos.qty <= 1e-9:
                pos.qty = 0.0
                pos.avg_entry = 0.0
            self.cash += fill.price * fill.qty - fill.fee
        if fill.price > 0:
            pos.mark = fill.price

    def apply_mark(self, bar: MarketBar) -> None:
        if bar.symbol in self.positions:
            self.positions[bar.symbol].mark = bar.close

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions.values())

    @property
    def gross_exposure(self) -> float:
        return sum(abs(p.market_value) for p in self.positions.values())

    @property
    def equity(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())

    def position_snapshots(self, ts: datetime | None = None) -> list[PositionSnapshot]:
        kwargs = {"ts": ts} if ts is not None else {}
        return [
            PositionSnapshot(
                symbol=p.symbol, qty=p.qty, avg_entry=p.avg_entry,
                mark=p.mark, unrealized_pnl=p.unrealized_pnl, **kwargs,
            )
            for p in self.positions.values()
            if p.qty != 0
        ]

    def equity_snapshot(self, ts: datetime | None = None) -> EquitySnapshot:
        kwargs = {"ts": ts} if ts is not None else {}
        return EquitySnapshot(
            equity=self.equity, cash=self.cash, gross_exposure=self.gross_exposure,
            realized_pnl_today=self.realized_pnl_today,
            unrealized_pnl=self.unrealized_pnl, **kwargs,
        )
