"""Multi-day scheduling: working hours, breaks, Sundays and spill-over to the next working day."""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, AppStatus, Round, RoundKind, Slot
from app.services.scheduler import Candidate, PanelSpec, SchedulingError, schedule, validate
from app.services.workhours import WorkHoursError, WorkingHours, parse_breaks
from tests.conftest import CSRF, login, make_cell_user, make_drive, make_student

IST = timezone(timedelta(minutes=330))


# ---------------------------------------------------------------- pure solver
def test_interviews_stay_inside_windows_and_spill_to_the_next_day() -> None:
    working = [(0, 120), (1440, 1560)]  # two 2-hour days
    cands = [Candidate(key=i) for i in range(1, 7)]
    panels = [PanelSpec(id=1)]
    result = schedule(cands, panels, 30, time_limit_sec=5, working=working)
    assert validate(result.assignments, cands, panels, working) == []
    starts = sorted(a.start for a in result.assignments)
    assert starts[:4] == [0, 30, 60, 90]           # day 1 is filled first
    assert starts[4:] == [1440, 1470]              # the rest open day 2


def test_no_interview_crosses_a_break() -> None:
    working = [(0, 50), (60, 120)]                 # a 10-minute break at 50
    cands = [Candidate(key=i) for i in range(1, 4)]
    result = schedule(cands, [PanelSpec(id=1)], 30, time_limit_sec=5, working=working)
    for a in result.assignments:
        assert (0 <= a.start and a.end <= 50) or (60 <= a.start and a.end <= 120)


def test_not_enough_working_time_is_a_clear_error() -> None:
    with pytest.raises(SchedulingError, match="don't fit"):
        schedule([Candidate(key=i) for i in range(5)], [PanelSpec(id=1)], 30, working=[(0, 120)])
    with pytest.raises(SchedulingError, match="long enough"):
        schedule([Candidate(key=1)], [PanelSpec(id=1)], 30, working=[(0, 20)])


def test_validate_flags_slots_outside_working_hours() -> None:
    from app.services.scheduler import Assignment
    c, p = [Candidate(key=1)], [PanelSpec(id=1)]
    assert validate([Assignment(1, 1, 100, 130)], c, p, [(0, 120)])  # runs past closing


# ---------------------------------------------------------------- working hours -> windows
def test_windows_skip_sundays_breaks_and_used_up_days() -> None:
    h = WorkingHours(day_start="09:00", day_end="18:00", breaks=("13:00-14:00",), max_days=2, skip_sundays=True)
    sat_evening = datetime(2026, 11, 14, 19, 0, tzinfo=IST).astimezone(UTC)
    days = h.working_days(sat_evening)
    assert [d.isoformat() for d in days] == ["2026-11-16", "2026-11-17"]   # Saturday is over, Sunday skipped
    w = h.windows(sat_evening)
    assert len(w) == 4
    first = sat_evening + timedelta(minutes=w[0][0])
    assert first.astimezone(IST).time() == time(9, 0)
    assert w[0][1] - w[0][0] == 240 and w[1][1] - w[1][0] == 240           # 09-13 and 14-18

    sat_morning = datetime(2026, 11, 14, 10, 30, tzinfo=IST).astimezone(UTC)
    assert h.windows(sat_morning)[0] == (0, 150)                            # today counts from now


def test_hours_are_validated() -> None:
    with pytest.raises(WorkHoursError):
        WorkingHours(day_start="18:00", day_end="09:00")
    with pytest.raises(WorkHoursError):
        parse_breaks("14:00-13:00")
    with pytest.raises(WorkHoursError):
        WorkingHours(max_days=0)
    assert WorkingHours.from_payload(WorkingHours().to_payload()) == WorkingHours()


# ---------------------------------------------------------------- end to end
def _waiting(db: Session, n: int) -> tuple[int, int]:
    d = make_drive(db, panels=2)
    for i in range(n):
        s = make_student(db, f"B2306{i:02d}CS", cgpa=8.0)
        db.add(Application(drive_id=d.id, student_id=s.id, status=AppStatus.in_process, rounds_cleared=1))
    db.flush()
    rnd = db.scalars(select(Round).where(Round.drive_id == d.id, Round.kind == RoundKind.interview)).first()
    assert rnd is not None
    rnd.duration_min = 30
    db.commit()
    return d.id, rnd.id


def test_api_schedules_a_round_over_two_days(client: TestClient, db: Session) -> None:
    drive_id, round_id = _waiting(db, 10)
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    tomorrow = (datetime.now(IST) + timedelta(days=1)).date()
    start = datetime.combine(tomorrow, time(8, 0), IST).astimezone(UTC).isoformat()
    body = {"round_id": round_id, "start_at": start, "day_start": "09:00", "day_end": "11:00", "breaks": [],
            "max_days": 2, "skip_sundays": False}

    r = client.post(f"/api/cell/drives/{drive_id}/schedule", json=body, headers=CSRF)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["slots"] == 10
    assert [d["slots"] for d in out["days"]] == [8, 2]      # 2 panels x 4 slots on day 1, the rest next day

    for s in db.scalars(select(Slot).where(Slot.round_id == round_id)):
        st = s.start_at.replace(tzinfo=s.start_at.tzinfo or UTC).astimezone(IST)
        en = s.end_at.replace(tzinfo=s.end_at.tzinfo or UTC).astimezone(IST)
        assert time(9, 0) <= st.time() and en.time() <= time(11, 0) and st.date() == en.date()

    # The hours are remembered for re-plans and shown back in the Schedule tab.
    hours = client.get(f"/api/cell/drives/{drive_id}/work-hours").json()
    assert hours["day_end"] == "11:00" and hours["max_days"] == 2

    too_tight = {**body, "max_days": 1}
    r = client.post(f"/api/cell/drives/{drive_id}/schedule", json=too_tight, headers=CSRF)
    assert r.status_code == 400 and "don't fit" in r.json()["detail"]

    bad = {**body, "day_start": "12:00"}
    assert client.post(f"/api/cell/drives/{drive_id}/schedule", json=bad, headers=CSRF).status_code == 400
