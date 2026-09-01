"""FastAPI bridge between the engine and the dashboard.

REST for history (served from the ledger), a websocket that fans out live bus
events, and the kill switch. Binds to localhost only — no auth in phase 1.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ..core.bus import EventBus
from ..core.events import Event, HaltEvent, ResumeEvent
from ..ledger.repo import LedgerRepo
from ..portfolio.accounting import Portfolio
from ..risk.gate import RiskGate

log = logging.getLogger(__name__)


class WebsocketFanout:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def on_event(self, event: Event) -> None:
        if not self.clients:
            return
        payload = json.dumps(event.to_dict(), default=str)
        dead = []
        for ws in self.clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


def build_app(bus: EventBus, repo: LedgerRepo, portfolio: Portfolio, gate: RiskGate) -> FastAPI:
    app = FastAPI(title="driftline")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    fanout = WebsocketFanout()
    bus.subscribe("*", fanout.on_event)

    @app.get("/status")
    def status() -> dict:
        return {
            "mode": "paper",
            "halted": gate.halted or gate.killed,
            "killed": gate.killed,
            "halt_reason": gate.halt_reason,
            "equity": portfolio.equity,
            "cash": portfolio.cash,
            "gross_exposure": portfolio.gross_exposure,
            "realized_pnl_today": portfolio.realized_pnl_today,
            "unrealized_pnl": portfolio.unrealized_pnl,
            "open_positions": len([p for p in portfolio.positions.values() if p.qty != 0]),
        }

    @app.get("/equity")
    def equity(hours: int = 24 * 30) -> list[dict]:
        return repo.equity_curve(since_hours=hours)

    @app.get("/positions")
    def positions() -> list[dict]:
        return [
            {"symbol": p.symbol, "qty": p.qty, "avg_entry": p.avg_entry,
             "mark": p.mark, "unrealized_pnl": p.unrealized_pnl,
             "market_value": p.market_value}
            for p in portfolio.positions.values() if p.qty != 0
        ]

    @app.get("/orders")
    def orders(limit: int = 200) -> list[dict]:
        return repo.orders(limit=limit)

    @app.get("/fills")
    def fills(limit: int = 200) -> list[dict]:
        return repo.fills(limit=limit)

    @app.get("/journal")
    def journal(limit: int = 200) -> list[dict]:
        return repo.journal(limit=limit)

    @app.get("/attribution")
    def attribution() -> list[dict]:
        return repo.attribution()

    @app.get("/halts")
    def halts() -> list[dict]:
        return repo.halts()

    @app.post("/kill")
    async def kill() -> dict:
        gate.kill("kill switch pressed on dashboard")
        await bus.publish(HaltEvent(source="manual", reason="kill switch pressed on dashboard"))
        return {"ok": True, "halted": True}

    @app.post("/resume")
    async def resume() -> dict:
        gate.resume("resumed from dashboard")
        await bus.publish(ResumeEvent(source="manual", reason="resumed from dashboard"))
        return {"ok": True, "halted": False}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        fanout.clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()  # keepalive pings from the client
        except WebSocketDisconnect:
            pass
        finally:
            fanout.clients.discard(websocket)

    return app
