"""End-to-end API tests: auth & security, the student flow, the recruiter link and a drive day."""
from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_checkin_token, create_link_token
from app.core.timeutil import utcnow
from app.models import Application, AppStatus, DriveStatus, ReplanProposal, Role, Slot
from tests.conftest import CSRF, login, make_cell_user, make_drive, make_student


# ---------------------------------------------------------------- auth & security
def test_otp_login_sets_httponly_cookie(client: TestClient, db: Session) -> None:
    make_cell_user(db)
    db.commit()
    r = client.post("/api/auth/otp/request", json={"email": "admin@nitc.ac.in"})
    code = r.json()["demo_code"]
    r = client.post("/api/auth/otp/verify", json={"email": "admin@nitc.ac.in", "code": code})
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/auth/me").json()["role"] == "admin"


def test_wrong_code_and_unknown_email(client: TestClient, db: Session) -> None:
    make_cell_user(db)
    db.commit()
    unknown = client.post("/api/auth/otp/request", json={"email": "nobody@nitc.ac.in"})
    assert unknown.status_code == 200 and unknown.json()["demo_code"] is None  # no account enumeration
    client.post("/api/auth/otp/request", json={"email": "admin@nitc.ac.in"})
    bad = client.post("/api/auth/otp/verify", json={"email": "admin@nitc.ac.in", "code": "000000"})
    assert bad.status_code == 400


def test_csrf_header_required_for_cookie_writes(client: TestClient, db: Session) -> None:
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    r = client.post("/api/cell/companies", json={"name": "Nova Labs"})
    assert r.status_code == 403
    r = client.post("/api/cell/companies", json={"name": "Nova Labs"}, headers=CSRF)
    assert r.status_code == 201


def test_role_checks(client: TestClient, db: Session) -> None:
    s = make_student(db, "B220001CS")
    db.commit()
    assert client.get("/api/cell/inbox").status_code == 401
    login(client, s.email)
    assert client.get("/api/cell/inbox").status_code == 403
    assert client.get("/api/me/dashboard").status_code == 200


def test_security_headers(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"] == "no-referrer"


def test_otp_rate_limit(client: TestClient, db: Session) -> None:
    make_cell_user(db)
    db.commit()
    codes = [client.post("/api/auth/otp/request", json={"email": "admin@nitc.ac.in"}).status_code
             for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[-1] == 429


# ---------------------------------------------------------------- student flow
def test_explained_eligibility_and_apply(client: TestClient, db: Session) -> None:
    ok = make_student(db, "B220001CS", cgpa=8.2)
    low = make_student(db, "B220002CS", cgpa=6.5, backlogs=1)
    d = make_drive(db, cgpa_min=7.0)
    db.commit()

    login(client, low.email)
    drives = client.get("/api/me/drives").json()
    entry = next(x for x in drives if x["drive"]["id"] == d.id)
    assert entry["eligible"] is False
    messages = " ".join(r["message"] for r in entry["reasons"] if not r["ok"])
    assert "6.5" in messages and "backlogs" in messages.lower()
    assert client.post(f"/api/me/drives/{d.id}/apply", json={}, headers=CSRF).status_code == 400
    r = client.post("/api/me/disputes", json={"drive_id": d.id, "message": "My backlog was cleared last week."},
                    headers=CSRF)
    assert r.status_code == 201
    client.post("/api/auth/logout", headers=CSRF)

    login(client, ok.email)
    r = client.post(f"/api/me/drives/{d.id}/apply", json={}, headers=CSRF)
    assert r.status_code == 201 and r.json()["status"] == "applied"
    dash = client.get("/api/me/dashboard").json()
    assert dash["funnel"][0] == {"stage": "Applied", "count": 1}


def test_student_cannot_edit_academics(client: TestClient, db: Session) -> None:
    s = make_student(db, "B220003CS", cgpa=7.0)
    db.commit()
    login(client, s.email)
    r = client.patch("/api/me/profile", json={"cgpa": 9.9, "skills": ["Rust", "go"]}, headers=CSRF)
    assert r.status_code == 200
    assert r.json()["cgpa"] == 7.0 and r.json()["skills"] == ["go", "rust"]


# ---------------------------------------------------------------- recruiter link
def test_recruiter_link_scopes_and_consent(client: TestClient, db: Session) -> None:
    applicant = make_student(db, "B220010CS", cgpa=8.5)
    make_student(db, "B220011CS", cgpa=8.0)
    d = make_drive(db, cgpa_min=7.0)
    db.add(Application(drive_id=d.id, student_id=applicant.id))
    db.commit()
    token = create_link_token("recruiter", d.id, d.link_version)
    h = {"X-Access-Token": token}

    assert client.get("/api/r/overview").status_code == 401
    ov = client.get("/api/r/overview", headers=h).json()
    assert ov["drive"]["company"] == "Acme Systems"
    applicants = client.get("/api/r/applicants", headers=h).json()
    assert [a["roll_no"] for a in applicants] == ["B220010CS"]  # non-applicants are never exposed

    preview = client.post("/api/r/pool-preview", json={"cgpa_min": 8.2}, headers=h).json()
    assert preview["current"] == 2 and preview["what_if"] == 1

    d.link_version += 1
    db.commit()
    assert client.get("/api/r/overview", headers=h).status_code == 401  # rotated links stop working


# ---------------------------------------------------------------- drive day
def _drive_day(db: Session, n: int = 4) -> tuple[int, list[int]]:
    d = make_drive(db, panels=2)
    app_ids = []
    for i in range(n):
        s = make_student(db, f"B22010{i}CS", cgpa=8.0 + i / 10)
        a = Application(drive_id=d.id, student_id=s.id, status=AppStatus.in_process, rounds_cleared=1)
        db.add(a)
        db.flush()
        app_ids.append(a.id)
    db.commit()
    return d.id, app_ids


def test_schedule_checkin_panel_flow(client: TestClient, db: Session) -> None:
    drive_id, _ = _drive_day(db)
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    detail = client.get(f"/api/cell/drives/{drive_id}").json()
    interview = next(r for r in detail["rounds"] if r["kind"] == "interview")
    start = (utcnow() - timedelta(minutes=5)).isoformat()
    r = client.post(f"/api/cell/drives/{drive_id}/schedule", json={"round_id": interview["id"], "start_at": start},
                    headers=CSRF)
    assert r.status_code == 200, r.text
    assert r.json()["slots"] == 4

    slots = client.get(f"/api/cell/drives/{drive_id}/slots").json()
    assert len(slots) == 4
    by_panel: dict[int, list[dict[str, object]]] = {}
    for s in slots:
        by_panel.setdefault(int(s["panel_id"]), []).append(s)  # type: ignore[arg-type]
    for items in by_panel.values():  # no panel double-booking
        items.sort(key=lambda s: str(s["start_at"]))
        for a, b in zip(items, items[1:], strict=False):
            assert str(a["end_at"]) <= str(b["start_at"])

    first = min(slots, key=lambda s: str(s["start_at"]))
    panel_token = create_link_token("panel", drive_id, 1, int(first["panel_id"]))  # type: ignore[arg-type]
    ph = {"X-Access-Token": panel_token}
    q = client.get("/api/p/queue", headers=ph).json()
    assert q["panel"]["round"] == "Technical interview"
    sid = q["waiting"][0]["id"]
    assert client.post(f"/api/p/slots/{sid}/start", headers=ph).status_code == 200
    r = client.post(f"/api/p/slots/{sid}/complete", json={"result": "pass"}, headers=ph)
    assert r.status_code == 200 and r.json()["status"] == "done"

    app = db.scalar(select(Application).join(Slot, Slot.application_id == Application.id).where(Slot.id == sid))
    db.refresh(app)
    assert app is not None and app.status == AppStatus.selected  # last round cleared


def test_student_venue_checkin(client: TestClient, db: Session) -> None:
    drive_id, app_ids = _drive_day(db, n=2)
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    detail = client.get(f"/api/cell/drives/{drive_id}").json()
    interview = next(r for r in detail["rounds"] if r["kind"] == "interview")
    client.post(f"/api/cell/drives/{drive_id}/schedule",
                json={"round_id": interview["id"], "start_at": (utcnow() + timedelta(minutes=20)).isoformat()},
                headers=CSRF)
    client.post("/api/auth/logout", headers=CSRF)

    app = db.get(Application, app_ids[0])
    assert app is not None
    login(client, app.student.email)
    r = client.post("/api/me/checkin", json={"token": create_checkin_token(drive_id)}, headers=CSRF)
    assert r.status_code == 200, r.text
    dash = client.get("/api/me/dashboard").json()
    assert dash["live"] and dash["live"][0]["checked_in"] is True


def test_no_show_creates_replan_proposal(client: TestClient, db: Session) -> None:
    drive_id, _ = _drive_day(db, n=4)
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    detail = client.get(f"/api/cell/drives/{drive_id}").json()
    interview = next(r for r in detail["rounds"] if r["kind"] == "interview")
    client.post(f"/api/cell/drives/{drive_id}/schedule",
                json={"round_id": interview["id"], "start_at": utcnow().isoformat()}, headers=CSRF)
    slots = sorted(client.get(f"/api/cell/drives/{drive_id}/slots").json(), key=lambda s: s["start_at"])
    for s in slots[1:]:  # everyone else is already at the venue
        assert client.post(f"/api/cell/drives/{drive_id}/slots/{s['id']}/checkin", headers=CSRF).status_code == 200
    r = client.post(f"/api/cell/drives/{drive_id}/slots/{slots[0]['id']}/no-show", headers=CSRF)
    assert r.status_code == 200
    db.expire_all()
    proposal = db.scalar(select(ReplanProposal).where(ReplanProposal.drive_id == drive_id))
    assert proposal is not None and proposal.changes  # the later candidate moves up
    r = client.post(f"/api/cell/drives/{drive_id}/proposals/{proposal.id}/approve", headers=CSRF)
    assert r.status_code == 200 and r.json()["applied"] >= 1
    inbox = client.get("/api/cell/inbox").json()
    assert all(i["kind"] != "replan" for i in inbox)


def test_opening_drive_requires_approved_rules(client: TestClient, db: Session) -> None:
    d = make_drive(db, status=DriveStatus.draft)
    d.rules[0].approved = False
    make_cell_user(db, Role.coordinator)
    db.commit()
    login(client, "coordinator@nitc.ac.in")
    r = client.patch(f"/api/cell/drives/{d.id}", json={"status": "open"}, headers=CSRF)
    assert r.status_code == 400
    client.post(f"/api/cell/drives/{d.id}/rules/approve-all", headers=CSRF)
    assert client.patch(f"/api/cell/drives/{d.id}", json={"status": "open"}, headers=CSRF).status_code == 200


def test_websocket_rejects_anonymous(client: TestClient) -> None:
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws") as ws:
        ws.receive_json()



def test_analytics_and_calendars(client: TestClient, db: Session) -> None:
    s = make_student(db, "B220050CS", cgpa=8.4, skills=["python", "sql"])
    d = make_drive(db, cgpa_min=7.0)
    db.add(Application(drive_id=d.id, student_id=s.id))
    make_cell_user(db)
    db.commit()

    h = {"X-Access-Token": create_link_token("recruiter", d.id, d.link_version)}
    ra = client.get("/api/r/analytics", headers=h).json()
    assert ra["totals"]["applicants"] == 1
    assert sum(b["count"] for b in ra["cgpa_histogram"]) == 1
    assert {c["skill"] for c in ra["skill_coverage"]} == {"python", "sql"}
    assert client.get("/api/r/schedule", headers=h).status_code == 200

    login(client, s.email)
    cal = client.get("/api/me/calendar").json()
    assert any(e["kind"] == "deadline" for e in cal)
    sa = client.get("/api/me/analytics").json()
    assert sa["cgpa"] == 8.4 and sa["cutoffs"][0]["cutoff"] == 7.0
    assert len(sa["weekly_applications"]) == 8
    client.post("/api/auth/logout", headers=CSRF)

    login(client, "admin@nitc.ac.in")
    season = client.get("/api/cell/analytics").json()
    assert season["drives"][0]["applied"] == 1
    assert client.get("/api/cell/calendar").status_code == 200
    links = client.get("/api/auth/demo-links").json()
    assert links and links[0]["path"].startswith("/r/")


def test_dashboard_shows_tomorrows_interview_and_hides_stale_ones(client: TestClient, db: Session) -> None:
    drive_id, app_ids = _drive_day(db, n=1)
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    detail = client.get(f"/api/cell/drives/{drive_id}").json()
    interview = next(r for r in detail["rounds"] if r["kind"] == "interview")
    client.post(f"/api/cell/drives/{drive_id}/schedule",
                json={"round_id": interview["id"], "start_at": (utcnow() + timedelta(hours=26)).isoformat()},
                headers=CSRF)
    client.post("/api/auth/logout", headers=CSRF)
    app = db.get(Application, app_ids[0])
    assert app is not None
    login(client, app.student.email)
    dash = client.get("/api/me/dashboard").json()
    assert len(dash["live"]) == 1                                  # tomorrow's interview gets the banner
    assert any(a["kind"] == "interview" for a in dash["actions"])
    slot = db.scalar(select(Slot).where(Slot.application_id == app.id))
    assert slot is not None
    slot.start_at = utcnow() - timedelta(days=2)                   # a stale slot from an earlier day
    slot.end_at = slot.start_at + timedelta(minutes=30)
    db.commit()
    dash = client.get("/api/me/dashboard").json()
    assert dash["live"] == [] and not any(a["kind"] == "interview" for a in dash["actions"])
