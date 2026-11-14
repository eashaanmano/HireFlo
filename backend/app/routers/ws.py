"""WebSocket endpoint for live updates. Auth: session cookie (students, cell) or ?token= (recruiter, panel)."""
from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from ..core.database import SessionLocal
from ..core.security import SESSION_COOKIE, TokenError, decode_link_token, decode_session_token
from ..models import Drive, Role, User
from ..services.events import LiveClient, manager

router = APIRouter()

HEARTBEAT_SEC = 25


def _authorise(websocket: WebSocket) -> LiveClient | None:
    token = websocket.query_params.get("token")
    with SessionLocal() as db:
        if token:
            for kind in ("recruiter", "panel"):
                try:
                    claims = decode_link_token(token, kind)  # type: ignore[arg-type]
                except TokenError:
                    continue
                drive = db.get(Drive, claims.drive_id)
                if drive is not None and drive.link_version == claims.version:
                    return LiveClient(websocket=websocket, drive_id=drive.id)
            return None
        cookie = websocket.cookies.get(SESSION_COOKIE)
        if not cookie:
            return None
        try:
            user = db.get(User, decode_session_token(cookie))
        except TokenError:
            return None
        if user is None or not user.active:
            return None
        student_id = user.student_id if user.role == Role.student else None
        return LiveClient(websocket=websocket, student_id=student_id)


@router.websocket("/ws")
async def live_updates(websocket: WebSocket) -> None:
    client = await asyncio.to_thread(_authorise, websocket)
    if client is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    manager.bind_loop(asyncio.get_running_loop())
    manager.register(client)

    async def pump() -> None:
        while True:
            try:
                notice = await asyncio.wait_for(client.queue.get(), timeout=HEARTBEAT_SEC)
            except TimeoutError:
                notice = {"type": "ping"}
            await websocket.send_json(notice)

    async def drain() -> None:
        while True:  # we don't expect client messages; reading detects disconnects promptly
            await websocket.receive_text()

    sender = asyncio.create_task(pump())
    receiver = asyncio.create_task(drain())
    try:
        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        manager.unregister(client)
        for task in (sender, receiver):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await task
