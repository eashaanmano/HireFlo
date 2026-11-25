"""Request dependencies: authentication and role-based authorisation, enforced server-side on every route."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..models import Drive, JobNotification, Panel, Role, Student, User
from .database import get_db
from .security import (
    SESSION_COOKIE,
    TokenError,
    decode_jnf_token,
    decode_link_token,
    decode_session_token,
)

DbSession = Annotated[Session, Depends(get_db)]


def _unauthorised(msg: str = "Sign in to continue.") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, msg)


def current_user(request: Request, db: DbSession) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _unauthorised()
    try:
        user_id = decode_session_token(token)
    except TokenError as exc:
        raise _unauthorised("Your session has expired. Sign in again.") from exc
    user = db.get(User, user_id)
    if user is None or not user.active:
        raise _unauthorised()
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_roles(*roles: Role) -> Callable[[User], User]:
    def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to this.")
        return user

    return dependency


CellUser = Annotated[User, Depends(require_roles(Role.coordinator, Role.admin))]
AdminUser = Annotated[User, Depends(require_roles(Role.admin))]


@dataclass
class StudentContext:
    user: User
    student: Student


def current_student(user: CurrentUser, db: DbSession) -> StudentContext:
    if user.role != Role.student or user.student_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Student access only.")
    student = db.get(Student, user.student_id)
    if student is None or student.archived:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your student profile is not active.")
    return StudentContext(user=user, student=student)


StudentCtx = Annotated[StudentContext, Depends(current_student)]


# ---------------------------------------------------------------- magic links
@dataclass
class RecruiterContext:
    drive: Drive
    actor: str


@dataclass
class PanelContext:
    drive: Drive
    panel: Panel
    actor: str


def _link_token(header: str | None, request: Request) -> str:
    token = header or request.query_params.get("token")
    if not token:
        raise _unauthorised("This link is missing its access token.")
    return token


FINISHED_DRIVE = ("completed", "cancelled")


def _require_active(drive: Drive) -> None:
    """Recruiter and panel links stop working once a drive is completed or cancelled."""
    state = getattr(drive.status, "value", drive.status)
    if state in FINISHED_DRIVE:
        raise _unauthorised(f"This drive is {state}, so its links no longer open. Contact the placement cell.")


def recruiter_context(request: Request, db: DbSession,
                      x_access_token: Annotated[str | None, Header()] = None) -> RecruiterContext:
    try:
        claims = decode_link_token(_link_token(x_access_token, request), "recruiter")
    except TokenError as exc:
        raise _unauthorised("This drive link is invalid or has expired. Ask the placement cell for a new one.") from exc
    drive = db.get(Drive, claims.drive_id)
    if drive is None or drive.link_version != claims.version:
        raise _unauthorised("This drive link was revoked. Ask the placement cell for a new one.")
    _require_active(drive)
    return RecruiterContext(drive=drive, actor=f"recruiter:{drive.company.name}")


def panel_context(request: Request, db: DbSession,
                  x_access_token: Annotated[str | None, Header()] = None) -> PanelContext:
    try:
        claims = decode_link_token(_link_token(x_access_token, request), "panel")
    except TokenError as exc:
        raise _unauthorised("This panel link is invalid or has expired.") from exc
    drive = db.get(Drive, claims.drive_id)
    panel = db.get(Panel, claims.panel_id) if claims.panel_id else None
    if drive is None or panel is None or panel.drive_id != drive.id or drive.link_version != claims.version:
        raise _unauthorised("This panel link was revoked.")
    _require_active(drive)
    return PanelContext(drive=drive, panel=panel, actor=f"panel:{panel.name}")


@dataclass
class JnfContext:
    jnf: JobNotification
    actor: str


def jnf_context(request: Request, db: DbSession,
                x_access_token: Annotated[str | None, Header()] = None) -> JnfContext:
    """A company's job notification form link: no account, one form, revocable by the cell."""
    try:
        jnf_id, version = decode_jnf_token(_link_token(x_access_token, request))
    except TokenError as exc:
        raise _unauthorised("This form link is invalid or has expired. Ask the placement cell for a new one.") from exc
    jnf = db.get(JobNotification, jnf_id)
    if jnf is None or jnf.link_version != version:
        raise _unauthorised("This form link was replaced. Ask the placement cell for the new one.")
    return JnfContext(jnf=jnf, actor=f"company:{jnf.company_name}")


RecruiterCtx = Annotated[RecruiterContext, Depends(recruiter_context)]
JnfCtx = Annotated[JnfContext, Depends(jnf_context)]
PanelCtx = Annotated[PanelContext, Depends(panel_context)]
