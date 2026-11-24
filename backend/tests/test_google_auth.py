"""Institute-only sign-in: OTP refuses other domains, and "Continue with Google" (OpenID Connect) for NITC accounts."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from tests.conftest import make_student


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
