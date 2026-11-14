"""Placement forecasting and early warning, company insight profiles, and the season retrospective report."""
from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_link_token
from app.core.timeutil import utcnow
from app.models import Application, AppStatus, DriveStatus, Event, Offer, OfferStatus
from app.services.company_profile import season_of
from tests.conftest import CSRF, login, make_cell_user, make_drive, make_student


def _season(db: Session) -> dict[str, int]:
    """A completed drive with history, an open drive, and students in two branches."""
    done = make_drive(db, ctc=12.0, status=DriveStatus.completed, company="Chip Works")
    done.drive_start = utcnow() - timedelta(days=30)
    nxt = make_drive(db, ctc=14.0, status=DriveStatus.open, company="Chip Works Two", cgpa_min=7.0)
    placed = make_student(db, "B220900CS", branch="CSE", cgpa=8.8)
    keen = make_student(db, "B220901CS", branch="CSE", cgpa=8.0)
    stuck = make_student(db, "B220902EC", branch="ECE", cgpa=6.2)          # below every cutoff
    others = [make_student(db, f"B22091{i}CS", branch="CSE", cgpa=7.5) for i in range(5)]
    win = Application(drive_id=done.id, student_id=placed.id, status=AppStatus.selected, rounds_cleared=2)
    db.add(win)
    for s in [keen, *others]:
        db.add(Application(drive_id=done.id, student_id=s.id, status=AppStatus.rejected, rounds_cleared=0))
    db.flush()
    db.add(Offer(application_id=win.id, ctc_lpa=12.0, deadline=utcnow() + timedelta(days=1),
                 status=OfferStatus.accepted))
    db.commit()
    from app.services import eligibility
    eligibility.recompute_drive(db, nxt)
    db.commit()
    return {"done": done.id, "next": nxt.id, "company": done.company_id, "stuck": stuck.id, "keen": keen.id,
            "placed": placed.id}


def test_season_label() -> None:
    from datetime import datetime
    assert season_of(datetime(2026, 8, 1)) == "2026-27"
    assert season_of(datetime(2027, 3, 1)) == "2026-27"


def test_forecast_and_early_warning(client: TestClient, db: Session) -> None:
    ids = _season(db)
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    f = client.get("/api/cell/forecast").json()
    cse = next(b for b in f["branches"] if b["branch"] == "CSE")
    assert cse["placed"] == 1 and cse["forecast_placed"] >= cse["placed"]
    assert 0 <= f["totals"]["forecast_pct"] <= 100 and f["basis"]["upcoming_drives"] >= 1
    ew = client.get("/api/cell/early-warning").json()
    stuck = next(r for r in ew if r["id"] == ids["stuck"])
    assert stuck["severity"] == "high" and "No eligible upcoming drive" in stuck["reasons"]
    assert all(r["id"] != ids["placed"] for r in ew)  # placed students are never flagged


def test_early_warning_is_cell_only(client: TestClient, db: Session) -> None:
    ids = _season(db)
    h = {"X-Access-Token": create_link_token("recruiter", ids["next"], 1)}
    assert client.get("/api/cell/early-warning", headers=h).status_code == 401


def test_company_profile_for_students(client: TestClient, db: Session) -> None:
    ids = _season(db)
    from app.models import Drive
    # a new open drive by the same company sees the history of its completed drive
    d = db.get(Drive, ids["next"])
    assert d is not None
    d.company_id = ids["company"]
    db.commit()
    from app.models import Student
    keen = db.get(Student, ids["keen"])
    assert keen is not None
    login(client, keen.email)
    r = client.get(f"/api/me/drives/{ids['next']}/company-profile")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["has_history"] and p["drives"] == 1 and p["ctc"] == {"min": 12.0, "max": 12.0}
    assert p["branches_hired"] == [{"branch": "CSE", "selected": 1}]
    assert p["stages"][0]["count"] == 7 and p["selection_rate"] == round(100 / 7, 1)
    assert "B220900CS" not in r.text  # aggregates only, no individual students


def test_retrospective_report_and_exports(client: TestClient, db: Session) -> None:
    _season(db)
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    r = client.get("/api/cell/reports/retrospective").json()
    assert r["summary"]["offers_accepted"] == 1 and r["summary"]["median_ctc"] == 12.0
    assert {t["tier"] for t in r["by_tier"]} >= {"Regular", "Dream", "PSU"}
    assert r["trends"] and r["available_seasons"]
    csv = client.get("/api/cell/reports/retrospective.csv")
    assert csv.status_code == 200 and csv.text.startswith("section,item,value")
    pdf = client.get("/api/cell/reports/retrospective.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert client.get("/api/cell/reports/retrospective", params={"season": "x" * 20}).status_code == 422
    client.post("/api/auth/logout", headers=CSRF)


def test_links_stop_working_after_drive_is_completed(client: TestClient, db: Session) -> None:
    d = make_drive(db, ctc=10.0, status=DriveStatus.closed, company="Link Co")
    db.commit()
    h = {"X-Access-Token": create_link_token("recruiter", d.id, d.link_version)}
    assert client.get("/api/r/overview", headers=h).status_code == 200  # closed: applications shut, drive ongoing
    d.status = DriveStatus.completed
    db.commit()
    r = client.get("/api/r/overview", headers=h)
    assert r.status_code == 401 and "completed" in r.text


def test_company_archive_restore_and_guards(client: TestClient, db: Session) -> None:
    d = make_drive(db, ctc=9.0, status=DriveStatus.open, company="Archive Co")
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    cid = d.company_id
    row = next(c for c in client.get("/api/cell/companies").json() if c["id"] == cid)
    assert row["active_drives"] == 1 and row["live_drives"] == 0
    # a live drive blocks archiving
    d.status = DriveStatus.live
    db.commit()
    assert client.delete(f"/api/cell/companies/{cid}", headers=CSRF).status_code == 409
    d.status = DriveStatus.open
    db.commit()
    assert client.delete(f"/api/cell/companies/{cid}", headers=CSRF).status_code == 204
    assert all(c["id"] != cid for c in client.get("/api/cell/companies").json())
    assert any(c["id"] == cid and c["archived"] for c in
               client.get("/api/cell/companies", params={"include_archived": True}).json())
    # the existing drive is untouched, but no new drive can be created for the company
    db.refresh(d)
    assert d.status == DriveStatus.open
    r = client.post("/api/cell/drives", json={"company_id": cid, "role": "Engineer", "ctc_lpa": 9.0}, headers=CSRF)
    assert r.status_code == 409 and "archived" in r.text
    assert client.post("/api/cell/companies", json={"name": "archive co"}, headers=CSRF).status_code == 409
    r = client.post(f"/api/cell/companies/{cid}/restore", headers=CSRF)
    assert r.status_code == 200 and r.json()["archived"] is False
    kinds = [e.type for e in db.scalars(select(Event).where(Event.entity == "company", Event.entity_id == cid))]
    assert "company.archived" in kinds and "company.restored" in kinds
