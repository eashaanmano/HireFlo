"""Event log + live fan-out over WebSockets.

Every state change goes through `emit()`. The row is the audit trail and analytics source; after the
transaction commits, a minimal notice (type + ids, never personal data) is pushed to every connected
screen so all three actors see one live stream and refetch what they need.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

from ..core.timeutil import utcnow
from ..models import Event

log = logging.getLogger("driveops.events")

_PENDING_KEY = "driveops_pending_events"


@dataclass(eq=False)
class LiveClient:
    websocket: WebSocket
    drive_id: int | None = None          # recruiter/panel links only see their own drive
    student_id: int | None = None        # students only get drive-level and own notices
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=256))

    def wants(self, notice: dict[str, Any]) -> bool:
        if self.drive_id is not None and notice.get("drive_id") not in (None, self.drive_id):
            return False
        target = notice.get("student_id")
        return not (self.student_id is not None and target is not None and target != self.student_id)


class ConnectionManager:
    def __init__(self) -> None:
        self.clients: set[LiveClient] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def register(self, client: LiveClient) -> None:
        self.clients.add(client)

    def unregister(self, client: LiveClient) -> None:
        self.clients.discard(client)

    def _fan_out(self, notice: dict[str, Any]) -> None:
        for client in list(self.clients):
            if not client.wants(notice):
                continue
            try:
                client.queue.put_nowait(notice)
            except asyncio.QueueFull:
                log.warning("dropping slow live client")
                self.clients.discard(client)

    def broadcast(self, notice: dict[str, Any]) -> None:
        """Thread-safe: callable from sync route handlers running in the threadpool."""
        if self.loop is None or self.loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self._fan_out(notice)
        else:
            self.loop.call_soon_threadsafe(self._fan_out, notice)


manager = ConnectionManager()


def emit(db: Session, type_: str, actor: str, *, entity: str | None = None, entity_id: int | None = None,
         drive_id: int | None = None, student_id: int | None = None,
         payload: dict[str, Any] | None = None) -> Event:
    """Record an event in the current transaction; broadcast it once the transaction commits."""
    ev = Event(ts=utcnow(), type=type_, actor=actor, entity=entity, entity_id=entity_id,
               drive_id=drive_id, payload=payload or {})
    db.add(ev)
    notice = {"type": type_, "entity": entity, "entity_id": entity_id, "drive_id": drive_id,
              "student_id": student_id, "ts": ev.ts.isoformat()}
    db.info.setdefault(_PENDING_KEY, []).append(notice)
    return ev


@sa_event.listens_for(Session, "after_commit")
def _flush_notices(session: Session) -> None:
    pending: list[dict[str, Any]] = session.info.pop(_PENDING_KEY, [])
    for notice in pending:
        manager.broadcast(notice)


@sa_event.listens_for(Session, "after_rollback")
def _drop_notices(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)
