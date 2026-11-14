"""Deadline nudges, the drive-day kit PDF, and slot clean-up on rejection."""
from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import utcnow
from app.models import Application, AppStatus, DriveStatus, Notification, Slot, SlotStatus
from app.services import eligibility, nudges
from tests.conftest import CSRF, login, make_cell_user, make_drive, make_student


def _open_drive(db: Session, hours_left: float):
    d = make_drive(db, ctc=8.0, status=DriveStatus.open, company="Nudge Co", cgpa_min=7.0)
    d.apply_deadline = utcnow() + timedelta(hours=hours_left)
    keen = make_student(db, "B230001CS", cgpa=8.5)
    idle = make_student(db, "B230002CS", cgpa=8.1)
    low = make_student(db, "B230003CS", cgpa=6.0)       # not eligible: never reminded
    db.flush()
    db.add(Application(drive_id=d.id, student_id=keen.id, status=AppStatus.applied))
    eligibility.recompute_drive(db, d)
    db.commit()
    return d, keen, idle, low


def _inbox(db: Session, sid: int) -> int:
    return len(db.scalars(select(Notification).where(Notification.student_id == sid)).all())


def test_nudge_reaches_only_eligible_non_applicants_once(db: Session) -> None:
    d, keen, idle, low = _open_drive(db, 10)
    before = {s.id: _inbox(db, s.id) for s in (keen, idle, low)}
    assert nudges.deadline_nudges(db) == 1
    db.commit()
    assert _inbox(db, idle.id) == before[idle.id] + 1
    assert _inbox(db, keen.id) == before[keen.id] and _inbox(db, low.id) == before[low.id]
    assert nudges.deadline_nudges(db) == 0          # never twice for the same drive
    db.commit()
    assert _inbox(db, idle.id) == before[idle.id] + 1


def test_no_nudge_while_deadline_is_far(db: Session) -> None:
    _open_drive(db, 72)
    assert nudges.deadline_nudges(db) == 0


def test_manual_nudge_endpoints(client: TestClient, db: Session) -> None:
    d, _keen, idle, _low = _open_drive(db, 60)
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    p = client.get(f"/api/cell/drives/{d.id}/nudge").json()
    assert p["pending"] == 1 and p["open"] and p["last_sent_at"] is None
    assert client.post(f"/api/cell/drives/{d.id}/nudge", headers=CSRF).json() == {"sent": 1}
    p = client.get(f"/api/cell/drives/{d.id}/nudge").json()
    assert p["last_count"] == 1 and p["last_sent_at"]
    d.status = DriveStatus.closed
    db.commit()
    assert client.post(f"/api/cell/drives/{d.id}/nudge", headers=CSRF).status_code == 400


def test_drive_kit_pdf(client: TestClient, db: Session) -> None:
    d = make_drive(db, status=DriveStatus.closed, company="Kit Co")
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    r = client.get(f"/api/cell/drives/{d.id}/drive-kit.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert "attachment" in r.headers["content-disposition"]


def test_reject_cancels_waiting_slots(client: TestClient, db: Session) -> None:
    d = make_drive(db, status=DriveStatus.closed, company="Reject Co")
    s = make_student(db, "B230010CS", cgpa=8.5)
    db.flush()
    a = Application(drive_id=d.id, student_id=s.id, status=AppStatus.in_process, rounds_cleared=1)
    db.add(a)
    db.flush()
    interview = d.rounds[1]
    db.add(Slot(drive_id=d.id, round_id=interview.id, panel_id=interview.panels[0].id, application_id=a.id,
                start_at=utcnow() + timedelta(hours=1), end_at=utcnow() + timedelta(hours=1, minutes=30)))
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    r = client.post(f"/api/cell/drives/{d.id}/applications/stage", json={"application_ids": [a.id], "action": "reject"},
                    headers=CSRF)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.scalar(select(Slot.status).where(Slot.application_id == a.id)) == SlotStatus.cancelled
