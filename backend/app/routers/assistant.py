"""HireFlo Assistant chat (streamed as server-sent events) and LLM provider status for the placement cell."""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from ..core.deps import CellUser, DbSession, StudentCtx
from ..core.ratelimit import limiter
from ..schemas import AssistantIn
from ..services import assistant, llm
from ..services.events import emit

router = APIRouter(tags=["assistant"])

PER_USER_PER_MIN = 10
ALL_USERS_PER_MIN = 25   # stays under Groq's free-tier 30 requests a minute


def _limit(key: str) -> None:
    if not limiter.hit(f"assistant:{key}", PER_USER_PER_MIN, 60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "You're sending messages quickly. Wait a minute.")
    if not limiter.hit("assistant:all", ALL_USERS_PER_MIN, 60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "The assistant is busy. Try again in a minute.")


def _sse(reply: assistant.Reply) -> StreamingResponse:
    def events() -> Iterator[str]:
        yield f"data: {json.dumps({'provider': reply.provider})}\n\n"
        for piece in reply.pieces:
            yield f"data: {json.dumps({'delta': piece})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _history(body: AssistantIn) -> list[dict[str, str]]:
    return [m.model_dump() for m in body.messages]


@router.post("/api/me/assistant")
def student_chat(body: AssistantIn, ctx: StudentCtx, db: DbSession) -> StreamingResponse:
    _limit(f"s{ctx.student.id}")
    try:
        reply = assistant.student_reply(db, ctx.student, _history(body))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    emit(db, "assistant.asked", ctx.user.email, entity="student", entity_id=ctx.student.id,
         payload={"provider": reply.provider})
    db.commit()
    return _sse(reply)


@router.post("/api/cell/assistant")
def cell_chat(body: AssistantIn, user: CellUser, db: DbSession) -> StreamingResponse:
    _limit(f"u{user.id}")
    try:
        reply = assistant.cell_reply(db, _history(body))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    emit(db, "assistant.asked", user.email, entity="user", entity_id=user.id, payload={"provider": reply.provider})
    db.commit()
    return _sse(reply)


@router.get("/api/cell/llm/status")
def llm_status(user: CellUser) -> dict[str, Any]:
    return llm.status()


@router.post("/api/cell/llm/test")
def llm_test(user: CellUser) -> list[dict[str, Any]]:
    if not limiter.hit(f"llmtest:{user.id}", 5, 60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Wait a minute before testing again.")
    return llm.test_providers()
