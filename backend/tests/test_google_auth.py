"""Institute-only sign-in: OTP refuses other domains, and "Continue with Google" (OpenID Connect) for NITC accounts."""
from __future__ import annotations

import base64
import hashlib
import logging
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import GOOGLE_FLOW_COOKIE
from app.models import Event, User
from app.services import google_oidc
from app.services.importer import import_students
from tests.conftest import CSRF, login, make_cell_user, make_student

CLIENT_ID = "hireflo-test.apps.googleusercontent.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)       # stands in for Google's signing key
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CALLBACK = "/api/auth/google/callback"


# ---------------------------------------------------------------- institute domain
def test_options_report_google_off_without_keys(client: TestClient) -> None:
    assert client.get("/api/auth/options").json() == {"google": False, "domain": "nitc.ac.in"}


def test_otp_refuses_addresses_outside_the_institute(client: TestClient, db: Session) -> None:
    make_student(db, "B230101CS")
    db.commit()
    for path, body in (("/api/auth/otp/request", {"email": "someone@gmail.com"}),
                       ("/api/auth/otp/verify", {"email": "someone@gmail.com", "code": "123456"})):
        r = client.post(path, json=body)
        assert r.status_code == 400, (path, r.text)
        assert r.json()["detail"] == "Use your @nitc.ac.in address."


def test_institute_check_ignores_case_and_spaces() -> None:
    s = Settings(institute_domain="nitc.ac.in")
    assert s.allows_email("  Eashaan_M260351CS@NITC.AC.IN ")
    assert not s.allows_email("eashaan@gmail.com")
    assert not s.allows_email("x@evilnitc.ac.in")


def test_blank_institute_domain_allows_any() -> None:
    assert Settings(institute_domain="").allows_email("a@b.com")


def test_cell_cannot_add_accounts_that_could_never_sign_in(client: TestClient, db: Session) -> None:
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    invite = client.post("/api/cell/users", headers=CSRF,
                         json={"email": "tpo.officer@gmail.com", "name": "TPO Officer", "role": "coordinator"})
    assert invite.status_code == 400 and invite.json()["detail"] == "Use an @nitc.ac.in address."
    student = client.post("/api/cell/students", headers=CSRF, json={
        "roll_no": "B230199CS", "name": "Outside Mail", "email": "outside@gmail.com", "branch": "CSE",
        "programme": "BTECH", "cgpa": 8.0, "grad_year": 2026})
    assert student.status_code == 400 and student.json()["detail"] == "Use an @nitc.ac.in address."
    ok = client.post("/api/cell/users", headers=CSRF,
                     json={"email": "Coord.New@NITC.ac.in", "name": "New Coordinator", "role": "coordinator"})
    assert ok.status_code == 201, ok.text


def test_import_flags_rows_outside_the_institute(db: Session) -> None:
    sheet = ("Roll Number,Student Name,Institute Email,Department,Degree,CPI,Active Backlogs,Batch\n"
             "B220010CS,Inside Mail,inside@nitc.ac.in,CSE,BTECH,8.1,0,2026\n"
             "B220011CS,Outside Mail,outside@gmail.com,CSE,BTECH,8.1,0,2026\n")
    report = import_students(db, sheet)
    assert report.created == 1
    assert [e.problems for e in report.errors] == [["not an @nitc.ac.in address 'outside@gmail.com'"]]


# ---------------------------------------------------------------- Google sign-in
class FakeGoogle:
    """Google's token endpoint and signing keys: answers the code exchange with an ID token signed by KEY."""

    def __init__(self) -> None:
        self.requests: list[dict[str, str]] = []
        self.nonce = ""
        self.claims: dict[str, Any] = {}

    def post_token(self, data: dict[str, str]) -> dict[str, Any]:
        self.requests.append(data)
        claims = dict(self.claims)
        if claims.pop("_down", False):
            raise httpx.ConnectError("Google is unreachable")
        if claims.pop("_refused", False):
            request = httpx.Request("POST", google_oidc.TOKEN_URL)
            reply = httpx.Response(401, request=request, json={"error": "invalid_client",
                                                               "error_description": "The OAuth client was not found."})
            raise httpx.HTTPStatusError("Client error '401 Unauthorized'", request=request, response=reply)
        key = OTHER_KEY if claims.pop("_other_key", False) else KEY
        now = int(time.time())
        payload = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "108177", "email_verified": True,
                   "hd": "nitc.ac.in", "nonce": self.nonce, "iat": now, "exp": now + 3600, "name": "Test"}
        payload.update(claims)
        token = jwt.encode({k: v for k, v in payload.items() if v is not None}, key, algorithm="RS256",
                           headers={"kid": "test"})
        return {"id_token": token, "access_token": "ya29.not-used"}


@pytest.fixture()
def google(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGoogle]:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    get_settings.cache_clear()
    fake = FakeGoogle()
    monkeypatch.setattr(google_oidc, "_post_token", fake.post_token)
    monkeypatch.setattr(google_oidc, "_signing_key", lambda token: KEY.public_key())
    yield fake
    monkeypatch.undo()
    get_settings.cache_clear()


def _start(client: TestClient, next_path: str | None = None) -> tuple[httpx.Response, dict[str, str]]:
    r = client.get("/api/auth/google/start", params={"next": next_path} if next_path else None,
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    return r, dict(parse_qsl(urlsplit(r.headers["location"]).query))


def _sign_in(client: TestClient, fake: FakeGoogle, next_path: str | None = None,
             **claims: Any) -> tuple[httpx.Response, dict[str, str]]:
    _, q = _start(client, next_path)
    fake.nonce, fake.claims = q["nonce"], claims
    r = client.get(CALLBACK, params={"code": "4/0AbcCode", "state": q["state"]}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r, q


def _student(db: Session, roll: str = "M260351CS") -> str:
    s = make_student(db, roll)
    db.commit()
    return s.email


def test_start_redirects_to_google(google: FakeGoogle, client: TestClient) -> None:
    assert client.get("/api/auth/options").json() == {"google": True, "domain": "nitc.ac.in"}
    r, q = _start(client, "/student")
    url = urlsplit(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == google_oidc.AUTH_URL
    assert q["client_id"] == CLIENT_ID and q["response_type"] == "code"
    assert q["redirect_uri"] == "http://testserver" + CALLBACK
    assert q["scope"] == "openid email profile" and q["hd"] == "nitc.ac.in" and q["prompt"] == "select_account"
    assert q["code_challenge_method"] == "S256" and q["state"] and q["nonce"]
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{GOOGLE_FLOW_COOKIE}=") and "HttpOnly" in cookie and "Path=/api/auth/google" in cookie


def test_redirect_uri_follows_the_address_in_the_browser(google: FakeGoogle, client: TestClient,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    # PUBLIC_URL may be a LAN address for phones (Google refuses raw IPs), and the flow cookie belongs to the host
    # the browser is on, so Google must send the user back to that same host.
    monkeypatch.setenv("PUBLIC_URL", "http://172.21.42.8:5173")
    get_settings.cache_clear()
    r = client.get("/api/auth/google/start", headers={"host": "localhost:5173"}, follow_redirects=False)
    q = dict(parse_qsl(urlsplit(r.headers["location"]).query))
    assert q["redirect_uri"] == "http://localhost:5173" + CALLBACK


def test_no_google_button_on_an_ip_address(google: FakeGoogle, client: TestClient) -> None:
    # Phones on the LAN open http://172.21.42.8:5173; Google refuses IP redirect URIs, so the button would dead-end.
    on_ip = client.get("/api/auth/options", headers={"host": "172.21.42.8:5173"}).json()
    assert on_ip == {"google": False, "domain": "nitc.ac.in"}
    assert client.get("/api/auth/options", headers={"host": "localhost:5173"}).json()["google"] is True


def test_callback_signs_in_a_registered_nitc_user(google: FakeGoogle, client: TestClient, db: Session) -> None:
    email = _student(db)
    r, q = _sign_in(client, google, "/student/offers", email=email)
    assert r.headers["location"] == "/student/offers"
    assert client.get("/api/auth/me").json()["email"] == email
    sent = google.requests[0]
    assert sent["code"] == "4/0AbcCode" and sent["client_id"] == CLIENT_ID and sent["client_secret"] == "test-secret"
    assert sent["redirect_uri"] == q["redirect_uri"] and sent["grant_type"] == "authorization_code"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(sent["code_verifier"].encode()).digest()).rstrip(b"=")
    assert challenge.decode() == q["code_challenge"]
    event = db.scalar(select(Event).where(Event.type == "auth.login").order_by(Event.id.desc()))
    assert event is not None and event.actor == email and event.payload == {"method": "google"}
    assert GOOGLE_FLOW_COOKIE not in client.cookies


def test_google_email_in_mixed_case_still_matches(google: FakeGoogle, client: TestClient, db: Session) -> None:
    email = _student(db)
    r, _ = _sign_in(client, google, email=email.upper())
    assert r.headers["location"] == "/"
    assert client.get("/api/auth/me").json()["email"] == email


@pytest.mark.parametrize(("claims", "reason"), [
    ({"hd": None}, "not_nitc"),                                        # a personal Google account
    ({"hd": "gmail.com"}, "not_nitc"),
    ({"email": "someone@gmail.com", "hd": None}, "not_nitc"),
    ({"email_verified": False}, "not_nitc"),
    ({"email": "nobody@nitc.ac.in"}, "not_registered"),
    ({"aud": "someone-else.apps.googleusercontent.com"}, "failed"),
    ({"iss": "https://evil.example"}, "failed"),
    ({"nonce": "replayed-nonce"}, "failed"),
    ({"exp": 1_000_000, "iat": 999_000}, "failed"),
    ({"_other_key": True}, "failed"),                                  # not signed by Google
    ({"_down": True}, "failed"),                                       # Google's token endpoint unreachable
])
def test_callback_rejects(google: FakeGoogle, client: TestClient, db: Session, claims: dict[str, Any],
                          reason: str) -> None:
    email = _student(db)
    r, _ = _sign_in(client, google, **({"email": email} | claims))
    assert r.headers["location"] == f"/login?error={reason}"
    assert client.get("/api/auth/me").status_code == 401


def test_failed_sign_in_is_logged_with_googles_reason_and_no_secret(google: FakeGoogle, client: TestClient,
                                                                     db: Session,
                                                                     caplog: pytest.LogCaptureFixture) -> None:
    # A mis-pasted secret on Render must be diagnosable from its Logs (warnings reach stderr without any setup).
    email = _student(db)
    with caplog.at_level(logging.INFO):
        r, _ = _sign_in(client, google, email=email, _refused=True)
    assert r.headers["location"] == "/login?error=failed"
    warnings = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any("invalid_client" in m and "The OAuth client was not found." in m for m in warnings), warnings
    assert "test-secret" not in caplog.text


def test_error_from_the_query_cannot_forge_log_lines(google: FakeGoogle, client: TestClient,
                                                     caplog: pytest.LogCaptureFixture) -> None:
    _start(client)
    with caplog.at_level(logging.INFO):
        client.get(CALLBACK, params={"error": "x\n[driveops] OTP for admin@nitc.ac.in: 123456"},
                   follow_redirects=False)
    assert caplog.records and all("\n" not in rec.getMessage() for rec in caplog.records)


def test_inactive_user_is_not_registered(google: FakeGoogle, client: TestClient, db: Session) -> None:
    email = _student(db)
    user = db.scalar(select(User).where(User.email == email))
    assert user is not None
    user.active = False
    db.commit()
    r, _ = _sign_in(client, google, email=email)
    assert r.headers["location"] == "/login?error=not_registered"


def test_state_mismatch_missing_cookie_or_reuse_is_expired(google: FakeGoogle, client: TestClient,
                                                           db: Session) -> None:
    email = _student(db)
    _, q = _start(client)
    google.nonce, google.claims = q["nonce"], {"email": email}
    forged = client.get(CALLBACK, params={"code": "c", "state": "forged"}, follow_redirects=False)
    assert forged.headers["location"] == "/login?error=expired"
    no_cookie = client.get(CALLBACK, params={"code": "c", "state": q["state"]}, follow_redirects=False)
    assert no_cookie.headers["location"] == "/login?error=expired"   # the failed try cleared the flow cookie

    ok, q2 = _sign_in(client, google, email=email)
    assert ok.headers["location"] == "/"
    back = client.get(CALLBACK, params={"code": "4/0AbcCode", "state": q2["state"]}, follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"] == "/login?error=expired"
    assert len(google.requests) == 1                                  # only the real sign-in reached Google


def test_cancelled_at_google(google: FakeGoogle, client: TestClient) -> None:
    _, q = _start(client)
    r = client.get(CALLBACK, params={"error": "access_denied", "state": q["state"]}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=cancelled"


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example", "/\\evil.example", "student"])
def test_unsafe_next_goes_home(google: FakeGoogle, client: TestClient, db: Session, target: str) -> None:
    email = _student(db)
    r, _ = _sign_in(client, google, target, email=email)
    assert r.headers["location"] == "/"


def test_google_routes_404_when_off(client: TestClient) -> None:
    assert client.get("/api/auth/google/start", follow_redirects=False).status_code == 404
    assert client.get(CALLBACK, params={"code": "c", "state": "s"}, follow_redirects=False).status_code == 404
