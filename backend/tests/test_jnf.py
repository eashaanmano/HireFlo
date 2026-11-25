"""Job Notification Form (JNF): a company fills it through a signed link; the cell approves it into a drive."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import create_link_token
from app.models import Company, Drive, DriveStatus, Event, Notification
from tests.conftest import CSRF, login, make_cell_user, make_drive, make_student

FORM: dict[str, Any] = {
    "role": "Engineer, Embedded Software", "ctc_lpa": 24, "base_lpa": 20, "variable_lpa": 2,
    "joining_bonus_lpa": 2, "location": "Hyderabad", "branches": ["cse", "ECE"], "programmes": [],
    "cgpa_min": 7.0, "max_backlogs": 0, "bond_months": 0, "interview_panels": 2, "target_hires": 1, "jd_text": "",
    "rounds": [{"name": "Online test", "kind": "test", "duration_min": 60},
               {"name": "Technical interview", "kind": "interview", "duration_min": 30}],
}


def _cell(client: TestClient, db: Session) -> None:
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")


def _students(db: Session) -> None:
    make_student(db, "B220001CS", branch="CSE", cgpa=8.5)
    make_student(db, "B220002CS", branch="CSE", cgpa=6.5)
    make_student(db, "B220003EC", branch="ECE", cgpa=7.5)
    make_student(db, "B220004ME", branch="ME", cgpa=9.0)
    db.commit()


def _invite(client: TestClient, company: str = "Qualcomm") -> tuple[int, dict[str, str]]:
    r = client.post("/api/cell/jnf", json={"company_name": company, "contact_name": "Asha (HR)",
                                           "contact_email": "hr@example.com"}, headers=CSRF)
    assert r.status_code == 201, r.text
    body = r.json()
    assert "/j/" in body["url"]
    return body["id"], {"X-Access-Token": body["url"].split("/j/")[1], **CSRF}


def _submit(client: TestClient, h: dict[str, str], **changes: Any) -> dict[str, Any]:
    r = client.post("/api/jnf/submit", json={**FORM, **changes}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- the company's side
def test_company_link_opens_the_form(client: TestClient, db: Session) -> None:
    _students(db)
    _cell(client, db)
    _, h = _invite(client)
    view = client.get("/api/jnf", headers=h).json()
    assert view["status"] == "invited" and view["company_name"] == "Qualcomm" and view["editable"] is True
    assert {"CSE", "ECE", "ME"} <= set(view["options"]["branches"])


def test_other_links_and_no_link_are_refused(client: TestClient, db: Session) -> None:
    d = make_drive(db)
    db.commit()
    assert client.get("/api/jnf").status_code == 401
    recruiter = create_link_token("recruiter", d.id, d.link_version)
    assert client.get("/api/jnf", headers={"X-Access-Token": recruiter}).status_code == 401


def test_pool_preview_counts_students_and_names_the_tier(client: TestClient, db: Session) -> None:
    _students(db)
    _cell(client, db)
    _, h = _invite(client)
    r = client.post("/api/jnf/pool-preview", json={"ctc_lpa": 24, "branches": ["cse", "ece"], "cgpa_min": 7},
                    headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"eligible": 2, "total_students": 4, "tier": "Super Dream"}


def test_submitted_form_reaches_the_cell_inbox(client: TestClient, db: Session) -> None:
    _students(db)
    _cell(client, db)
    jid, h = _invite(client)
    assert _submit(client, h)["status"] == "submitted"
    item = next(i for i in client.get("/api/cell/inbox").json() if i["kind"] == "jnf")
    assert item["id"] == jid and "Qualcomm" in item["title"] and "Super Dream" in item["detail"]
    review = client.get(f"/api/cell/jnf/{jid}").json()["review"]
    assert review["tier"] == "Super Dream" and review["eligible"] == 2 and review["flags"] == []


def test_review_flags_what_needs_a_look(client: TestClient, db: Session) -> None:
    _students(db)
    other = make_drive(db, company="Intel")
    other.drive_start = datetime(2026, 12, 10, 4, 0, tzinfo=UTC)  # 09:30 IST on 10 Dec
    db.commit()
    _cell(client, db)
    jid, h = _invite(client)
    _submit(client, h, bond_months=12, bond_note="Training bond", base_lpa=10, variable_lpa=8,
            joining_bonus_lpa=None, preferred_date="2026-12-10", target_hires=5)
    flags = " | ".join(client.get(f"/api/cell/jnf/{jid}").json()["review"]["flags"])
    assert "Bond of 12 months" in flags
    assert "Variable pay is 33% of the CTC" in flags
    assert "adds up to 18 LPA, not 24 LPA" in flags
    assert "Intel also has a drive on 10 Dec 2026" in flags
    assert "Only 2 eligible students for 5 hires" in flags


# ---------------------------------------------------------------- the cell's decision
def test_approval_creates_and_opens_the_drive(client: TestClient, db: Session) -> None:
    _students(db)
    _cell(client, db)
    jid, h = _invite(client)
    _submit(client, h, preferred_date="2026-12-10", apply_by="2026-12-05")
    r = client.post(f"/api/cell/jnf/{jid}/approve", json={"open_now": True}, headers=CSRF)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["opened"] is True

    d = db.get(Drive, out["drive_id"])
    assert d is not None and d.status == DriveStatus.open and d.company.name == "Qualcomm"
    assert (d.role, d.ctc_lpa, d.location, d.target_hires) == ("Engineer, Embedded Software", 24, "Hyderabad", 1)
    assert [(x.name, x.kind.value, x.duration_min, x.seq) for x in d.rounds] == [
        ("Online test", "test", 60, 1), ("Technical interview", "interview", 30, 2)]
    rules = {x.field: (x.op, x.value, x.approved) for x in d.rules}
    assert rules == {"cgpa": (">=", 7.0, True), "backlogs": ("<=", 0, True), "branch": ("in", ["CSE", "ECE"], True)}

    told = db.scalars(select(Notification).where(Notification.title == "New drive: Qualcomm")).all()
    assert len(told) == 2  # the two eligible students
    recruiter = {"X-Access-Token": out["recruiter_url"].split("/r/")[1]}
    assert client.get("/api/r/overview", headers=recruiter).status_code == 200
    assert db.scalar(select(Event).where(Event.type == "jnf.approved")) is not None

    view = client.get("/api/jnf", headers=h).json()
    assert view["status"] == "approved" and view["editable"] is False
    again = client.post("/api/jnf/submit", json=FORM, headers=h)
    assert again.status_code == 409 and "approved" in again.json()["detail"]
    assert client.post(f"/api/cell/jnf/{jid}/approve", json={"open_now": True}, headers=CSRF).status_code == 409


def test_existing_company_is_reused_whatever_the_case(client: TestClient, db: Session) -> None:
    existing = make_drive(db, company="AMD").company
    db.commit()
    _cell(client, db)
    jid, h = _invite(client, company="amd")
    _submit(client, h)
    out = client.post(f"/api/cell/jnf/{jid}/approve", json={"open_now": False}, headers=CSRF).json()
    assert db.get(Drive, out["drive_id"]).company_id == existing.id  # type: ignore[union-attr]
    assert db.scalar(select(func.count()).select_from(Company).where(func.lower(Company.name) == "amd")) == 1


def test_archived_company_blocks_approval(client: TestClient, db: Session) -> None:
    db.add(Company(name="Intel", archived=True))
    db.commit()
    _cell(client, db)
    jid, h = _invite(client, company="Intel")
    _submit(client, h)
    r = client.post(f"/api/cell/jnf/{jid}/approve", json={"open_now": True}, headers=CSRF)
    assert r.status_code == 409 and "archived" in r.json()["detail"]


def test_rules_found_only_in_the_jd_keep_the_drive_a_draft(client: TestClient, db: Session) -> None:
    _students(db)
    _cell(client, db)
    jid, h = _invite(client)
    _submit(client, h, max_backlogs=None,
            jd_text="Candidates need a minimum CGPA of 8.0 and no active backlogs.")
    out = client.post(f"/api/cell/jnf/{jid}/approve", json={"open_now": True}, headers=CSRF).json()
    assert out["opened"] is False
    d = db.get(Drive, out["drive_id"])
    assert d is not None and d.status == DriveStatus.draft
    cgpa = [r for r in d.rules if r.field == "cgpa"]
    assert [(r.value, r.approved) for r in cgpa] == [(7.0, True)]  # the form's own value wins over the JD's
    assert [(r.field, r.approved) for r in d.rules if r.field == "backlogs"] == [("backlogs", False)]


def test_changes_requested_then_resubmitted_then_declined(client: TestClient, db: Session) -> None:
    _cell(client, db)
    jid, h = _invite(client)
    _submit(client, h)
    r = client.post(f"/api/cell/jnf/{jid}/request-changes", json={"note": "Please add the bond details."},
                    headers=CSRF)
    assert r.status_code == 200, r.text
    view = client.get("/api/jnf", headers=h).json()
    assert view["status"] == "changes_requested" and view["cell_note"] == "Please add the bond details."
    assert view["editable"] is True
    assert _submit(client, h, bond_months=6)["status"] == "submitted"

    r = client.post(f"/api/cell/jnf/{jid}/decline", json={"note": "Season is full."}, headers=CSRF)
    assert r.status_code == 200
    view = client.get("/api/jnf", headers=h).json()
    assert view["status"] == "declined" and view["editable"] is False
    assert client.post("/api/jnf/submit", json=FORM, headers=h).status_code == 409


def test_a_new_link_revokes_the_old_one(client: TestClient, db: Session) -> None:
    _cell(client, db)
    jid, old = _invite(client)
    new_url = client.post(f"/api/cell/jnf/{jid}/rotate", headers=CSRF).json()["url"]
    assert client.get("/api/jnf", headers=old).status_code == 401
    assert client.get("/api/jnf", headers={"X-Access-Token": new_url.split("/j/")[1]}).status_code == 200


def test_students_cannot_send_or_review_forms(client: TestClient, db: Session) -> None:
    make_student(db, "B220009CS")
    db.commit()
    login(client, "b220009cs@nitc.ac.in")
    assert client.post("/api/cell/jnf", json={"company_name": "X Corp"}, headers=CSRF).status_code == 403
    assert client.get("/api/cell/jnf").status_code == 403


def test_bad_forms_are_rejected(client: TestClient, db: Session) -> None:
    _cell(client, db)
    _, h = _invite(client)
    assert client.post("/api/jnf/submit", json={**FORM, "rounds": []}, headers=h).status_code == 422
    assert client.post("/api/jnf/submit", json={**FORM, "ctc_lpa": 0}, headers=h).status_code == 422
