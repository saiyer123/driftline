"""Async in-process event bus with an append-only JSONL log.

Single source of truth for component wiring: subscribers register per event
type (or "*"), publishers fire-and-forget. Every published event is appended
to the JSONL event log before subscribers run, so the log is complete even if
a subscriber crashes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Awaitable, Callable

from .events import Event

log = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self, log_path: Path | None = None):
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._log_path = log_path
        self._log_file = None
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(log_path, "a", encoding="utf-8")

    def subscribe(self, event_type: str | type[Event], handler: Handler) -> None:
        key = event_type if isinstance(event_type, str) else event_type.__name__
        self._subs[key].append(handler)

    async def publish(self, event: Event) -> None:
        if self._log_file is not None:
            self._log_file.write(json.dumps(event.to_dict(), default=str) + "\n")
            self._log_file.flush()
        handlers = self._subs.get(event.type, []) + self._subs.get("*", [])
        for h in handlers:
            try:
                await h(event)
            except Exception:
                log.exception("handler %s failed for %s", h, event.type)

    async def publish_many(self, events: list[Event]) -> None:
        for e in events:
            await self.publish(e)

    def close(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
