"""Deadline nudges: remind eligible students who have not applied that a drive closes soon.

Each drive is nudged automatically at most once (an event records it); the cell can also send one by hand.
Only students the eligibility engine marks eligible, who are active and have no live application, are reminded.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.timeutil import ensure_utc, utcnow
from ..models import Application, AppStatus, Drive, DriveStatus, EligibilityDecision, Event, Student
from .events import emit
from .notifications import notify, send_email

AUTO = "drive.deadline_nudge"


def pending_students(db: Session, drive: Drive) -> list[Student]:
    """Eligible, active students without an application (a withdrawn one counts as none)."""
    applied = set(db.scalars(select(Application.student_id).where(Application.drive_id == drive.id,
                                                                  Application.status != AppStatus.withdrawn)))
    rows = db.scalars(select(Student).join(EligibilityDecision, EligibilityDecision.student_id == Student.id)
                      .where(EligibilityDecision.drive_id == drive.id, EligibilityDecision.eligible.is_(True),
                             Student.archived.is_(False)).order_by(Student.roll_no))
    return [s for s in rows if s.id not in applied]


def _hours_left(drive: Drive, now: datetime) -> float | None:
    if drive.apply_deadline is None:
        return None
    return (ensure_utc(drive.apply_deadline) - now).total_seconds() / 3600  # type: ignore[operator]


def send_nudge(db: Session, drive: Drive, actor: str, now: datetime | None = None) -> int:
    now = now or utcnow()
    students = pending_students(db, drive)
    if not students:
        return 0
    left = _hours_left(drive, now)
    when = f"in about {max(1, round(left))} hours" if left is not None and left > 0 else "soon"
    title = f"{drive.company.name} closes {when}"
    body = (f"You're eligible for {drive.company.name} ({drive.role}, {drive.ctc_lpa:g} LPA) and haven't applied yet. "
            f"Applications close {when}.")
    notify(db, [s.id for s in students], title, body, link=f"/student/drives/{drive.id}")
    for s in students:
        send_email(s.email, title, body + f"\n\nOpen HireFlo: {get_settings().public_url}/student/drives/{drive.id}")
    emit(db, AUTO if actor == "system" else "drive.manual_nudge", actor, entity="drive", entity_id=drive.id,
         drive_id=drive.id, payload={"students": len(students)})
    return len(students)


def deadline_nudges(db: Session, now: datetime | None = None) -> int:
    """Run from the background monitor: nudge each open drive once when its deadline is near."""
    hours = get_settings().nudge_hours_before
    if hours <= 0:
        return 0
    now = now or utcnow()
    done = set(db.scalars(select(Event.entity_id).where(Event.type == AUTO)))
    sent = 0
    for d in db.scalars(select(Drive).where(Drive.status == DriveStatus.open, Drive.apply_deadline.is_not(None))):
        left = _hours_left(d, now)
        if d.id in done or left is None or not 0 < left <= hours:
            continue
        n = send_nudge(db, d, "system", now)
        sent += n
        if n == 0:  # nobody to remind; still record it so the drive is not checked every tick
            emit(db, AUTO, "system", entity="drive", entity_id=d.id, drive_id=d.id, payload={"students": 0})
    return sent


def last_nudge(db: Session, drive: Drive) -> Event | None:
    return db.scalar(select(Event).where(Event.entity == "drive", Event.entity_id == drive.id,
                                         Event.type.in_((AUTO, "drive.manual_nudge")))
                     .order_by(Event.id.desc()).limit(1))

