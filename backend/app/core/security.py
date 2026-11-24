"""Tokens and secrets: session JWTs, drive-scoped magic links, signed check-in codes, OTP hashing."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

import jwt

from .config import get_settings
from .timeutil import utcnow

ALGORITHM = "HS256"
SESSION_COOKIE = "driveops_session"
CSRF_HEADER = "X-DriveOps-CSRF"

GOOGLE_FLOW_COOKIE = "driveops_google"
GOOGLE_FLOW_TTL = timedelta(minutes=10)

TokenKind = Literal["session", "recruiter", "panel", "checkin", "google_flow"]


class TokenError(Exception):
    """Raised for any invalid, expired or wrong-kind token."""


@dataclass(frozen=True)
class LinkClaims:
    kind: TokenKind
    drive_id: int
    version: int
    panel_id: int | None = None


def _encode(claims: dict[str, Any], ttl: timedelta) -> str:
    now = utcnow()
    payload = {**claims, "iat": int(now.timestamp()), "exp": int((now + ttl).timestamp())}
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def _decode(token: str, kind: TokenKind) -> dict[str, Any]:
    try:
        data: dict[str, Any] = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if data.get("kind") != kind:
        raise TokenError("wrong token kind")
    return data


# ---------------------------------------------------------------- sessions
def create_session_token(user_id: int, role: str) -> str:
    hours = get_settings().session_hours
    return _encode({"kind": "session", "sub": str(user_id), "role": role}, timedelta(hours=hours))


def decode_session_token(token: str) -> int:
    return int(_decode(token, "session")["sub"])


# ---------------------------------------------------------------- Google sign-in
def create_google_flow_token(state: str, nonce: str, verifier: str, next_path: str) -> str:
    """What the callback needs to finish a Google sign-in, kept in a signed, short-lived, HttpOnly cookie."""
    return _encode({"kind": "google_flow", "state": state, "nonce": nonce, "verifier": verifier, "next": next_path},
                   GOOGLE_FLOW_TTL)


def decode_google_flow_token(token: str) -> dict[str, str]:
    data = _decode(token, "google_flow")
    try:
        return {k: str(data[k]) for k in ("state", "nonce", "verifier", "next")}
    except KeyError as exc:
        raise TokenError("incomplete sign-in state") from exc


# ---------------------------------------------------------------- magic links
def create_link_token(kind: Literal["recruiter", "panel"], drive_id: int, version: int,
                      panel_id: int | None = None) -> str:
    claims: dict[str, Any] = {"kind": kind, "drive": drive_id, "v": version}
    if panel_id is not None:
        claims["panel"] = panel_id
    return _encode(claims, timedelta(days=get_settings().recruiter_link_days))


def decode_link_token(token: str, kind: Literal["recruiter", "panel"]) -> LinkClaims:
    data = _decode(token, kind)
    return LinkClaims(kind=kind, drive_id=int(data["drive"]), version=int(data["v"]),
                      panel_id=int(data["panel"]) if "panel" in data else None)


# ---------------------------------------------------------------- venue check-in (rotating QR)
CHECKIN_WINDOW_SEC = 90


def create_checkin_token(drive_id: int) -> str:
    """Short-lived token shown as a QR at the venue; students scan it to check in."""
    return _encode({"kind": "checkin", "drive": drive_id, "n": secrets.token_hex(4)},
                   timedelta(seconds=CHECKIN_WINDOW_SEC))


def decode_checkin_token(token: str) -> int:
    return int(_decode(token, "checkin")["drive"])


# ---------------------------------------------------------------- OTP
def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(email: str, code: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, f"{email.lower()}:{code}".encode(), hashlib.sha256).hexdigest()


def verify_otp(email: str, code: str, code_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(email, code), code_hash)
