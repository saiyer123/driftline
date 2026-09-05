"""Typed events — the only way components talk to each other.

Every event that flows on the bus is one of these dataclasses. They are
JSON-serializable via `to_dict` so the same objects feed the append-only
event log, the ledger, and the dashboard websocket.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    VETOED = "vetoed"    # killed by the risk gate; never reached the broker
    APPROVED = "approved"  # gate-approved and persisted; about to be sent to the broker


@dataclass
class Event:
    ts: datetime = field(default_factory=utcnow, kw_only=True)
    event_id: str = field(default_factory=new_id, kw_only=True)

    @property
    def type(self) -> str:
        return type(self).__name__

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
            elif isinstance(v, Enum):
                d[k] = v.value
        return d


@dataclass
class MarketBar(Event):
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    bar_ts: datetime  # bar period end, exchange time (UTC)
    feed: str = "iex"  # data provenance: "iex" (free Alpaca), "sip", "replay"


@dataclass
class Signal(Event):
    strategy: str
    strategy_version: str
    symbol: str
    reasoning: str
    target_weight: float  # desired fraction of equity in this symbol


@dataclass
class OrderIntent(Event):
    strategy: str
    strategy_version: str
    symbol: str
    side: Side
    qty: float
    reasoning: str = ""
    intent_id: str = field(default_factory=new_id)


@dataclass
class OrderUpdate(Event):
    intent_id: str
    broker_order_id: str
    symbol: str
    side: Side
    qty: float
    status: OrderStatus
    strategy: str
    strategy_version: str
    reason: str = ""  # rejection / veto reason


@dataclass
class Fill(Event):
    intent_id: str
    broker_order_id: str
    symbol: str
    side: Side
    qty: float
    price: float
    fee: float
    strategy: str
    strategy_version: str


@dataclass
class PositionSnapshot(Event):
    symbol: str
    qty: float
    avg_entry: float
    mark: float
    unrealized_pnl: float


@dataclass
class EquitySnapshot(Event):
    equity: float
    cash: float
    gross_exposure: float
    realized_pnl_today: float
    unrealized_pnl: float


@dataclass
class HaltEvent(Event):
    source: str  # "risk_gate" | "reconcile" | "manual"
    reason: str


@dataclass
class ResumeEvent(Event):
    source: str
    reason: str


@dataclass
class ResearchSignal(Event):
    """A bounded parameter published by the cognition plane.

    The engine consumes these as clamped inputs (never as orders). kind is
    "regime" (key: "market") or "symbol_tilt" (key: a ticker); value is
    bounded per kind at both write and read time.
    """
    kind: str
    key: str
    value: float
    confidence: float
    reasoning: str
    source_model: str


@dataclass
class JournalEntry(Event):
    strategy: str
    strategy_version: str
    kind: str  # "decision" | "note"
    text: str
    payload: dict = field(default_factory=dict)
