from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from app.repository.report_repository import ReportRepository

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    SCAN_START = "scan_start"
    SCAN_END = "scan_end"
    BUY_EVAL = "buy_eval"
    SELL_EVAL = "sell_eval"
    ORDER_EXEC = "order_exec"
    PRE_MARKET = "pre_market"
    POST_MARKET = "post_market"
    ERROR = "error"
    STATE_CHANGE = "state_change"


@dataclass
class BotEvent:
    type: EventType
    message: str
    timestamp: float = field(default_factory=time.time)
    data: dict | None = None


class EventBus:
    def __init__(self, max_history: int = 200) -> None:
        self._subscribers: list[asyncio.Queue[BotEvent]] = []
        self._history: deque[BotEvent] = deque(maxlen=max_history)
        self._error_history: deque[BotEvent] = deque(maxlen=50)
        self._report_repo: ReportRepository | None = None

    def set_report_repo(self, repo: ReportRepository) -> None:
        self._report_repo = repo

    def emit(self, event: BotEvent) -> None:
        self._history.append(event)
        if event.type == EventType.ERROR:
            self._error_history.append(event)

        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)

        for q in dead:
            self._subscribers.remove(q)

        if self._report_repo is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._persist_event(event))
            except RuntimeError:
                pass

    async def _persist_event(self, event: BotEvent) -> None:
        if self._report_repo is None:
            return
        try:
            await self._report_repo.save_bot_event(
                event.type.value, event.message, event.timestamp, event.data,
            )
        except Exception:
            logger.debug("이벤트 DB 저장 실패", exc_info=True)

    async def subscribe(self) -> AsyncIterator[BotEvent]:
        q: asyncio.Queue[BotEvent] = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        try:
            while True:
                event = await q.get()
                yield event
        finally:
            if q in self._subscribers:
                self._subscribers.remove(q)

    @property
    def recent_events(self) -> list[BotEvent]:
        return list(self._history)

    @property
    def recent_errors(self) -> list[BotEvent]:
        return list(self._error_history)


_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
