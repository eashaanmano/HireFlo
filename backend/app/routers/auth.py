"""Passwordless sign-in: e-mail OTP for students and the placement cell. No passwords are ever stored."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select, update

from ..core.config import get_settings
from ..core.deps import CurrentUser, DbSession
from ..core.ratelimit import limiter, rate_limit
from ..core.security import (
    SESSION_COOKIE,
    create_link_token,
    create_session_token,
    generate_otp,
    hash_otp,
    verify_otp,
)
from ..core.timeutil import utcnow
from ..models import Drive, DriveStatus, OtpCode, Role, User
from ..schemas import DemoAccount, Me, OtpRequest, OtpRequested, OtpVerify
from ..services.events import emit
from ..services.notifications import send_email

router = APIRouter(prefix="/api/auth", tags=["auth"])

GENERIC = "If that address is registered, a 6-digit code is on its way."


@router.post("/otp/request", response_model=OtpRequested,
             dependencies=[Depends(rate_limit("otp-request", 20, 600))])
def request_otp(body: OtpRequest, db: DbSession) -> OtpRequested:
    settings = get_settings()
    email = body.email.lower()
    if not limiter.hit(f"otp-email:{email}", 5, 600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many codes requested. Wait a few minutes.")
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.active:
        # Same response as success so the endpoint can't be used to discover registered e-mails.
        return OtpRequested(sent=True, message=GENERIC)
    db.execute(update(OtpCode).where(OtpCode.email == email, OtpCode.used.is_(False)).values(used=True))
    code = generate_otp()
    db.add(OtpCode(email=email, code_hash=hash_otp(email, code),
                   expires_at=utcnow() + timedelta(minutes=settings.otp_ttl_minutes)))
    db.commit()
    delivered = send_email(email, "Your HireFlo sign-in code",
                           f"Your code is {code}. It expires in {settings.otp_ttl_minutes} minutes.")
    if not delivered:
        print(f"[driveops] OTP for {email}: {code}", flush=True)  # noqa: T201 — the server log is the fallback
    if settings.demo_mode:
        return OtpRequested(sent=True, demo_code=code, message="Demo mode: the code is shown below.")
    return OtpRequested(sent=True, message=GENERIC)


@router.post("/otp/verify", response_model=Me, dependencies=[Depends(rate_limit("otp-verify", 30, 600))])
def verify(body: OtpVerify, response: Response, db: DbSession) -> User:
    settings = get_settings()
    email = body.email.lower()
    otp = db.scalar(select(OtpCode).where(OtpCode.email == email, OtpCode.used.is_(False))
                    .order_by(OtpCode.id.desc()))
    invalid = HTTPException(status.HTTP_400_BAD_REQUEST, "That code is wrong or has expired. Request a new one.")
    if otp is None or otp.expires_at < utcnow() or otp.attempts >= settings.otp_max_attempts:
        raise invalid
    if not verify_otp(email, body.code, otp.code_hash):
        otp.attempts += 1
        db.commit()
        raise invalid
    otp.used = True
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.active:
        raise invalid
    emit(db, "auth.login", user.email, entity="user", entity_id=user.id)
    db.commit()
    response.set_cookie(
        SESSION_COOKIE, create_session_token(user.id, user.role.value), httponly=True,
        secure=settings.cookie_secure, samesite="lax", max_age=settings.session_hours * 3600, path="/",
    )
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=Me)
def me(user: CurrentUser) -> User:
    return user


@router.get("/demo-links")
def demo_links(db: DbSession) -> list[dict[str, str]]:
    """Recruiter and panel links for the landing page, so evaluators can open the recruiting-team view."""
    if not get_settings().demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    out: list[dict[str, str]] = []
    drives = db.scalars(select(Drive).where(Drive.status.in_((DriveStatus.live, DriveStatus.open, DriveStatus.closed)))
                        .order_by(Drive.ctc_lpa.desc())).all()
    live_first = sorted(drives, key=lambda d: d.status != DriveStatus.live)[:3]
    for d in live_first:
        out.append({"kind": "recruiter", "title": f"{d.company.name} recruiting team",
                    "note": f"{d.role}, {d.status.value}",
                    "path": f"/r/{create_link_token('recruiter', d.id, d.link_version)}"})
    live = next((d for d in drives if d.status == DriveStatus.live), None)
    if live is not None:
        panel = next((p for r in live.rounds for p in r.panels), None)
        if panel is not None:
            out.append({"kind": "panel", "title": f"{live.company.name} interviewer ({panel.name})",
                        "note": "One-screen panel view: Start, Done",
                        "path": f"/p/{create_link_token('panel', live.id, live.link_version, panel.id)}"})
    return out


@router.get("/demo-accounts", response_model=list[DemoAccount])
def demo_accounts(db: DbSession) -> list[DemoAccount]:
    """Lists sample sign-ins so evaluators can switch roles quickly. Disabled outside demo mode."""
    if not get_settings().demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    picks: list[DemoAccount] = []
    for role, note in ((Role.admin, "Placement cell admin"), (Role.coordinator, "Placement coordinator")):
        u = db.scalar(select(User).where(User.role == role, User.active.is_(True)).order_by(User.id))
        if u:
            picks.append(DemoAccount(email=u.email, name=u.name, role=u.role, note=note))
    featured = db.scalars(select(User).where(User.role == Role.student, User.active.is_(True))
                          .order_by(User.id).limit(3)).all()
    notes = ["Checked in for today's AMD interview", "Pending Accenture offer below 10 LPA",
             "Placed at Cognizant; 1 of 2 upgrade chances used"]
    for u, note in zip(featured, notes, strict=False):
        picks.append(DemoAccount(email=u.email, name=u.name, role=u.role, note=note))
    return picks

