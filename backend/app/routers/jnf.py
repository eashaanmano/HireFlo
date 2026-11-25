"""The company's side of a Job Notification Form: one signed, revocable link, no account.

The company sees its own form and aggregate counts only.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..core.deps import DbSession, JnfCtx
from ..core.ratelimit import rate_limit
from ..schemas import JnfCriteria, JnfForm
from ..services import jnf
from .common import conflict

router = APIRouter(prefix="/api/jnf", tags=["company form link"], dependencies=[Depends(rate_limit("jnf", 120, 60))])


@router.get("")
def get_form(ctx: JnfCtx, db: DbSession) -> dict[str, Any]:
    return jnf.public_view(db, ctx.jnf)


@router.post("/pool-preview")
def pool_preview(body: JnfCriteria, ctx: JnfCtx, db: DbSession) -> dict[str, Any]:
    """How many students would meet these criteria (a count, never names)."""
    return jnf.pool_count(db, body)


@router.post("/submit")
def submit(body: JnfForm, ctx: JnfCtx, db: DbSession) -> dict[str, Any]:
    try:
        jnf.submit(db, ctx.jnf, body, ctx.actor)
    except jnf.JnfError as exc:
        raise conflict(str(exc)) from exc
    db.commit()
    return jnf.public_view(db, ctx.jnf)
