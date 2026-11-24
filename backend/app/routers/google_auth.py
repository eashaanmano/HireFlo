"""Continue with Google: the browser goes to Google and comes back to /callback, which signs a NITC user in."""
from __future__ import annotations

import hmac
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..core.config import Settings, get_settings
from ..core.deps import DbSession
from ..core.ratelimit import rate_limit
from ..core.security import (
    GOOGLE_FLOW_COOKIE,
    GOOGLE_FLOW_TTL,
    TokenError,
    create_google_flow_token,
    decode_google_flow_token,
)
from ..models import User
from ..services import google_oidc
from ..services.events import emit
from ..services.google_oidc import GoogleSignInError
from .auth import set_session_cookie

router = APIRouter(prefix="/api/auth/google", tags=["auth"])
log = logging.getLogger(__name__)
FLOW_PATH = "/api/auth/google"


def safe_next(path: str | None) -> str:
    """Only a path on this site: never another host (`//x`, `https://x`, `/\\x`), so the redirect can't be abused."""
    if not path or not path.startswith("/") or path.startswith("//") or any(c in path for c in "\\\r\n"):
        return "/"
    return path


def _google_settings() -> Settings:
    settings = get_settings()
    if not settings.google_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Google sign-in is not set up.")
    return settings


def _callback_url(request: Request) -> str:
    # The address the browser is on (behind Render's proxy, uvicorn's --proxy-headers make this https), not
    # PUBLIC_URL: that may be a LAN IP for phones, which Google refuses, and the flow cookie belongs to this host.
    return str(request.base_url).rstrip("/") + FLOW_PATH + "/callback"


@router.get("/start", dependencies=[Depends(rate_limit("google-start", 30, 600))])
def start(request: Request, next: str | None = None) -> RedirectResponse:  # noqa: A002 — the login page's ?next=
    settings = _google_settings()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = google_oidc.new_pkce()
    url = google_oidc.authorization_url(settings, redirect_uri=_callback_url(request), state=state, nonce=nonce,
                                        code_challenge=challenge)
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(GOOGLE_FLOW_COOKIE, create_google_flow_token(state, nonce, verifier, safe_next(next)),
                        max_age=int(GOOGLE_FLOW_TTL.total_seconds()), httponly=True, secure=settings.cookie_secure,
                        samesite="lax", path=FLOW_PATH)
    return response


def _finish(db: DbSession, settings: Settings, request: Request, code: str | None, state: str | None,
            error: str | None) -> tuple[User, str]:
    flow_cookie = request.cookies.get(GOOGLE_FLOW_COOKIE)
    if error:
        raise GoogleSignInError("cancelled" if error == "access_denied" else "failed", error)
    if not flow_cookie:
        raise GoogleSignInError("expired", "no sign-in in progress on this browser")
    try:
        flow = decode_google_flow_token(flow_cookie)
    except TokenError as exc:
        raise GoogleSignInError("expired", str(exc)) from exc
    if not state or not hmac.compare_digest(state, flow["state"]):
        raise GoogleSignInError("expired", "state mismatch")
    if not code:
        raise GoogleSignInError("failed", "no authorization code")
    token = google_oidc.exchange_code(settings, code=code, code_verifier=flow["verifier"],
                                      redirect_uri=_callback_url(request))
    identity = google_oidc.verify_id_token(settings, token, nonce=flow["nonce"])
    user = db.scalar(select(User).where(User.email == identity.email))
    if user is None or not user.active:
        raise GoogleSignInError("not_registered", identity.email)
    emit(db, "auth.login", user.email, entity="user", entity_id=user.id, payload={"method": "google"})
    db.commit()
    return user, safe_next(flow["next"])


@router.get("/callback", dependencies=[Depends(rate_limit("google-callback", 30, 600))])
def callback(request: Request, db: DbSession, code: str | None = None, state: str | None = None,
             error: str | None = None) -> RedirectResponse:
    settings = _google_settings()
    try:
        user, next_path = _finish(db, settings, request, code, state, error)
    except GoogleSignInError as exc:
        # Unexpected failures are warnings, which reach Render's Logs without any logging setup; %r keeps a
        # crafted ?error= on one line.
        level = logging.WARNING if exc.reason == "failed" else logging.INFO
        log.log(level, "Google sign-in refused (%s): %r", exc.reason, exc.detail[:300])
        response = RedirectResponse(f"/login?error={exc.reason}", status_code=status.HTTP_303_SEE_OTHER)
    else:
        response = RedirectResponse(next_path, status_code=status.HTTP_303_SEE_OTHER)
        set_session_cookie(response, user)
    response.delete_cookie(GOOGLE_FLOW_COOKIE, path=FLOW_PATH)  # one try per start: Back can't replay it
    return response
