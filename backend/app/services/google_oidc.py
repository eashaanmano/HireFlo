"""Google sign-in: OpenID Connect authorization-code flow with PKCE, and ID-token checks.

Google is NIT Calicut's own identity provider (nitc.ac.in runs on Google Workspace), so "Continue with Google" is the
institute's single sign-on. Only the identity is used: the access token Google returns is thrown away.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
import jwt

from ..core.config import Settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPE = "openid email profile"
TIMEOUT_SEC = 10.0

Reason = Literal["cancelled", "not_nitc", "not_registered", "expired", "failed"]


class GoogleSignInError(Exception):
    """A sign-in that must not go through; `reason` is what the login page explains."""

    def __init__(self, reason: Reason, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason: Reason = reason
        self.detail = detail


@dataclass(frozen=True)
class GoogleIdentity:
    email: str
    name: str
    sub: str


def new_pkce() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorization_url(settings: Settings, *, redirect_uri: str, state: str, nonce: str, code_challenge: str) -> str:
    params = {"client_id": settings.google_client_id.strip(), "redirect_uri": redirect_uri, "response_type": "code",
              "scope": SCOPE, "state": state, "nonce": nonce, "code_challenge": code_challenge,
              "code_challenge_method": "S256", "prompt": "select_account"}
    if settings.domain:
        params["hd"] = settings.domain  # only narrows Google's account picker; the ID token's hd claim is checked
    return f"{AUTH_URL}?{urlencode(params)}"


def _post_token(data: dict[str, str]) -> dict[str, Any]:
    r = httpx.post(TOKEN_URL, data=data, timeout=TIMEOUT_SEC)
    r.raise_for_status()
    body: dict[str, Any] = r.json()
    return body


def exchange_code(settings: Settings, *, code: str, code_verifier: str, redirect_uri: str) -> str:
    """Swaps the authorization code for Google's ID token (server to server, authenticated by the client secret)."""
    try:
        body = _post_token({"code": code, "client_id": settings.google_client_id.strip(),
                            "client_secret": settings.google_client_secret.strip(), "redirect_uri": redirect_uri,
                            "grant_type": "authorization_code", "code_verifier": code_verifier})
    except httpx.HTTPStatusError as exc:
        raise GoogleSignInError("failed", f"Google refused the code exchange: {_google_error(exc.response)}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise GoogleSignInError("failed", f"code exchange: {exc}") from exc
    token = body.get("id_token")
    if not isinstance(token, str) or not token:
        raise GoogleSignInError("failed", "no ID token in Google's reply")
    return token


def _google_error(response: httpx.Response) -> str:
    """Google's own reason (invalid_client, invalid_grant, redirect_uri_mismatch...), never our request."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return f"HTTP {response.status_code}"
    return f"HTTP {response.status_code} {body.get('error', '')}: {body.get('error_description', '')}"


@lru_cache(maxsize=1)
def _jwk_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(JWKS_URL, cache_keys=True, timeout=int(TIMEOUT_SEC))


def _signing_key(token: str) -> Any:
    return _jwk_client().get_signing_key_from_jwt(token).key


def verify_id_token(settings: Settings, token: str, *, nonce: str) -> GoogleIdentity:
    """Checks Google's signature, issuer, audience, expiry and nonce, then that the account is the institute's."""
    try:
        claims: dict[str, Any] = jwt.decode(token, _signing_key(token), algorithms=["RS256"],
                                            audience=settings.google_client_id.strip(), leeway=60,
                                            options={"require": ["iss", "aud", "exp", "iat", "sub"]})
    except (jwt.PyJWTError, ValueError) as exc:
        raise GoogleSignInError("failed", f"ID token: {exc}") from exc
    if claims.get("iss") not in ISSUERS:
        raise GoogleSignInError("failed", "ID token from another issuer")
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise GoogleSignInError("failed", "nonce mismatch")
    email = str(claims.get("email", "")).strip().lower()
    if not email or claims.get("email_verified") not in (True, "true"):
        raise GoogleSignInError("not_nitc", "e-mail not verified by Google")
    # Google's docs: trust the hd claim, not the e-mail's domain, to tell an organisation's accounts apart.
    if settings.domain and (claims.get("hd") != settings.domain or not settings.allows_email(email)):
        raise GoogleSignInError("not_nitc", f"hd={claims.get('hd')!r}")
    return GoogleIdentity(email=email, name=str(claims.get("name", "")), sub=str(claims["sub"]))
