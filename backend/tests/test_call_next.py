"""The "you're up next" alert: one heads-up per interview, shortly before it, from the same projection students see."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_link_token
from app.core.timeutil import ensure_utc, utcnow
from app.models import (
    Application,
    AppStatus,
    Drive,
    DriveStatus,
    Event,
    Notification,
    ProposalStatus,
    ReplanProposal,
    RoundKind,
    Slot,
)
from app.services import live
from tests.conftest import make_drive, make_student


def _scheduled_drive(db: Session, n: int, panels: int, start_in_min: int) -> Drive:
    d = make_drive(db, panels=panels, status=DriveStatus.live)
    for i in range(n):
        s = make_student(db, f"B22030{i}CS", cgpa=8.0 + i / 10)
        db.add(Application(drive_id=d.id, student_id=s.id, status=AppStatus.in_process, rounds_cleared=1))
    db.flush()
    db.refresh(d)
    interview = next(r for r in d.rounds if r.kind == RoundKind.interview)
    live.schedule_round(db, d, interview, utcnow() + timedelta(minutes=start_in_min), "test")
    db.commit()
    return d


def _called_slot_ids(db: Session) -> list[int]:
    return [int(e.entity_id) for e in db.scalars(select(Event).where(Event.type == "slot.called"))
            if e.entity_id is not None]


def _notes(db: Session, student_id: int) -> list[Notification]:
    """The student's "you're up" alerts (scheduling a round sends its own notification too)."""
    return [n for n in db.scalars(select(Notification).where(Notification.student_id == student_id))
            if "you're up" in n.title]


def test_calls_students_close_to_their_turn_once(db: Session) -> None:
    d = _scheduled_drive(db, n=6, panels=2, start_in_min=1)
    now = utcnow()
    slots = list(db.scalars(select(Slot).where(Slot.drive_id == d.id)))
    # 3 per panel at about +1, +31 and +61 min: the third on each panel is over the 60-minute cap.
    expected = {s.id for s in slots if ensure_utc(s.start_at) <= now + timedelta(minutes=60)}
    assert len(expected) == 4

    assert live.call_next(db, d, now) == 4
    db.commit()
    assert set(_called_slot_ids(db)) == expected

    first = min(slots, key=lambda s: ensure_utc(s.start_at))
    notes = _notes(db, first.application.student_id)
    assert len(notes) == 1
    assert "you're up next" in notes[0].title
    assert first.panel.room in notes[0].body

    assert live.call_next(db, d, now) == 0  # never twice for the same interview
    assert len(_called_slot_ids(db)) == 4


def test_does_not_call_a_far_future_interview(db: Session) -> None:
    d = _scheduled_drive(db, n=1, panels=1, start_in_min=180)
    assert live.call_next(db, d) == 0
    assert _called_slot_ids(db) == []


def test_students_not_checked_in_are_told_to_check_in(db: Session) -> None:
    d = _scheduled_drive(db, n=2, panels=2, start_in_min=1)
    here, away = list(db.scalars(select(Slot).where(Slot.drive_id == d.id)))
    live.check_in(db, here, "test")
    live.call_next(db, d)
    db.commit()
    assert "Check in" not in _notes(db, here.application.student_id)[0].body
    assert "Check in" in _notes(db, away.application.student_id)[0].body


def test_panel_marking_done_calls_the_next_student(client: TestClient, db: Session) -> None:
    d = _scheduled_drive(db, n=4, panels=1, start_in_min=-100)  # behind schedule: everyone is projected from now
    slots = sorted(db.scalars(select(Slot).where(Slot.drive_id == d.id)), key=lambda s: ensure_utc(s.start_at))
    live.call_next(db, d)
    db.commit()
    last_student = slots[-1].application.student_id
    assert _notes(db, last_student) == []  # three ahead: not yet

    h = {"X-Access-Token": create_link_token("panel", d.id, d.link_version, slots[0].panel_id)}
    assert client.post(f"/api/p/slots/{slots[0].id}/start", headers=h).status_code == 200
    assert client.post(f"/api/p/slots/{slots[0].id}/complete", json={"result": "fail"}, headers=h).status_code == 200

    db.expire_all()
    notes = _notes(db, last_student)
    assert len(notes) == 1 and slots[-1].panel.room in notes[0].body


def test_monitor_and_approved_replans_run_the_alert(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _scheduled_drive(db, n=2, panels=1, start_in_min=1)
    seen: list[int] = []
    monkeypatch.setattr(live, "call_next", lambda db, drive, now=None: seen.append(drive.id) or 0)

    live.monitor_tick(db)
    assert seen == [d.id]

    p = ReplanProposal(drive_id=d.id, reason="test", changes=[], metrics={}, status=ProposalStatus.pending)
    db.add(p)
    db.flush()
    live.apply_proposal(db, p, "cell")
    assert seen == [d.id, d.id]
