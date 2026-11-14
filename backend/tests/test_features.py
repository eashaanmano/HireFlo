"""Disruption re-planning, the offer decision simulator and the fairness audit."""
from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.timeutil import utcnow
from app.models import Application, AppStatus, Company, Offer, Slot, SlotStatus
from app.services.eligibility import RuleSpec
from app.services.fairness import rule_label
from app.services.live import project_panel
from app.services.offer_sim import OddsModel, p_at_least_one
from tests.conftest import CSRF, login, make_cell_user, make_drive, make_student


# ---------------------------------------------------------------- pure helpers
def test_p_at_least_one() -> None:
    assert p_at_least_one([]) == 0.0
    assert p_at_least_one([0.5, 0.5]) == 0.75


def test_odds_are_clamped() -> None:
    m = OddsModel({"Dream": 0.9}, personal_factor=1.8)
    assert m.odds("Dream") == 0.85
    assert OddsModel({}, personal_factor=0.01).odds("Regular") == 0.02


def test_rule_labels() -> None:
    assert rule_label(RuleSpec("cgpa", ">=", 7.5)) == "CGPA >= 7.5"
    assert rule_label(RuleSpec("backlogs", "<=", 0)) == "At most 0 active backlog(s)"
    assert rule_label(RuleSpec("branch", "in", ["CSE", "ECE"])).endswith("CSE, ECE")


def test_hold_pushes_projection() -> None:
    now = utcnow()
    slots = [Slot(id=i, status=SlotStatus.scheduled, start_at=now + timedelta(minutes=30 * i),
                  end_at=now + timedelta(minutes=30 * (i + 1))) for i in range(2)]
    free = project_panel(slots, 30, now)
    held = project_panel(slots, 30, now, hold_until=now + timedelta(minutes=45))
    assert free[0].lateness_min == 0
    assert held[0].lateness_min == 45 and held[1].lateness_min == 45


# ---------------------------------------------------------------- disruptions
def _scheduled_day(client: TestClient, db: Session, n: int = 6) -> tuple[int, list[dict[str, object]]]:
    d = make_drive(db, panels=3)
    for i in range(n):
        s = make_student(db, f"B22030{i}CS", cgpa=8.0)
        db.add(Application(drive_id=d.id, student_id=s.id, status=AppStatus.in_process, rounds_cleared=1))
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    detail = client.get(f"/api/cell/drives/{d.id}").json()
    interview = next(r for r in detail["rounds"] if r["kind"] == "interview")
    r = client.post(f"/api/cell/drives/{d.id}/schedule",
                    json={"round_id": interview["id"], "start_at": (utcnow() + timedelta(minutes=20)).isoformat()},
                    headers=CSRF)
    assert r.status_code == 200, r.text
    return d.id, interview["panels"]


def test_panel_down_moves_its_queue(client: TestClient, db: Session) -> None:
    drive_id, panels = _scheduled_day(client, db)
    down = int(panels[0]["id"])  # type: ignore[call-overload]
    r = client.post(f"/api/cell/drives/{drive_id}/disrupt", json={"kind": "panel_down", "panel_id": down},
                    headers=CSRF)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] >= 1 and body["solve_ms"] >= 0
    slots = client.get(f"/api/cell/drives/{drive_id}/slots").json()
    assert all(s["panel_id"] != down for s in slots if s["status"] == "scheduled")
    room = client.get(f"/api/cell/drives/{drive_id}/control-room").json()
    assert next(p for p in room["panels"] if p["id"] == down)["hold"] == {"until": None}

    r = client.post(f"/api/cell/drives/{drive_id}/disrupt", json={"kind": "release", "panel_id": down}, headers=CSRF)
    assert r.status_code == 200
    room = client.get(f"/api/cell/drives/{drive_id}/control-room").json()
    assert next(p for p in room["panels"] if p["id"] == down)["hold"] is None


def test_overrun_and_no_show(client: TestClient, db: Session) -> None:
    drive_id, panels = _scheduled_day(client, db)
    r = client.post(f"/api/cell/drives/{drive_id}/disrupt",
                    json={"kind": "overrun", "panel_id": panels[0]["id"], "minutes": 60}, headers=CSRF)
    assert r.status_code == 200, r.text
    assert r.json()["disruption"].endswith("60 min over")
    r = client.post(f"/api/cell/drives/{drive_id}/disrupt", json={"kind": "no_show"}, headers=CSRF)
    assert r.status_code == 200, r.text
    slots = client.get(f"/api/cell/drives/{drive_id}/slots").json()
    assert sum(s["status"] == "no_show" for s in slots) == 1


def test_disrupt_validates_panel(client: TestClient, db: Session) -> None:
    drive_id, _ = _scheduled_day(client, db, n=2)
    r = client.post(f"/api/cell/drives/{drive_id}/disrupt", json={"kind": "overrun"}, headers=CSRF)
    assert r.status_code == 400


# ---------------------------------------------------------------- offer simulator
def test_offer_what_if(client: TestClient, db: Session) -> None:
    s = make_student(db, "B220070CS", cgpa=8.5)
    low = make_drive(db, ctc=6.0, company="Steady Services")
    high = make_drive(db, ctc=14.0, company="Chip Works")
    psu = make_drive(db, ctc=9.0, company="State Power")
    db.get(Company, psu.company_id).sector = "PSU"  # type: ignore[union-attr]
    app = Application(drive_id=low.id, student_id=s.id, status=AppStatus.selected)
    db.add(app)
    db.flush()
    db.add(Offer(application_id=app.id, ctc_lpa=6.0, deadline=utcnow() + timedelta(days=1)))
    db.commit()
    offer = db.query(Offer).one()

    login(client, s.email)
    r = client.get(f"/api/me/offers/{offer.id}/what-if")
    assert r.status_code == 200, r.text
    w = r.json()
    assert w["offer"]["ends_season"] is False and w["chances"]["left_after_accept"] == 2
    rows = {d["company"]: d for d in w["drives"]}
    assert rows["Chip Works"]["eligible_after"] is True and rows["Chip Works"]["uses_chance"] is True
    assert rows["State Power"]["eligible_now"] is True
    assert 0 <= w["summary"]["accept_upgrade_odds"] <= 1
    assert w["summary"]["decline_better_targets"]
    assert high.id in {d["drive_id"] for d in w["drives"]}


def test_offer_what_if_is_private(client: TestClient, db: Session) -> None:
    owner = make_student(db, "B220071CS")
    other = make_student(db, "B220072CS")
    d = make_drive(db, ctc=6.0)
    app = Application(drive_id=d.id, student_id=owner.id, status=AppStatus.selected)
    db.add(app)
    db.flush()
    db.add(Offer(application_id=app.id, ctc_lpa=6.0, deadline=utcnow() + timedelta(days=1)))
    db.commit()
    offer = db.query(Offer).one()
    login(client, other.email)
    assert client.get(f"/api/me/offers/{offer.id}/what-if").status_code == 404


# ---------------------------------------------------------------- fairness audit
def test_fairness_audit(client: TestClient, db: Session) -> None:
    d = make_drive(db, cgpa_min=7.5)
    for i in range(6):
        make_student(db, f"B22080{i}CS", branch="CSE", cgpa=8.2)
    for i in range(6):
        make_student(db, f"B22081{i}EC", branch="ECE", cgpa=7.45 if i < 4 else 8.0)
    make_student(db, "B220820ME", branch="ME", cgpa=8.0, backlogs=1)
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    r = client.get(f"/api/cell/drives/{d.id}/fairness")
    assert r.status_code == 200, r.text
    f = r.json()
    assert f["totals"]["considered"] == 13 and f["totals"]["eligible"] == 8
    ece = next(b for b in f["branches"] if b["branch"] == "ECE")
    assert ece["ratio"] is not None and ece["ratio"] < 0.8
    assert len(f["near_miss"]) == 4 and f["near_miss"][0]["gap"] == 0.05
    assert any("four-fifths" in fl["text"] for fl in f["flags"])
    assert any("backlogs" in fl["text"] for fl in f["flags"])
    assert next(s for s in f["sweep"] if s["current"])["eligible"] == 8
    cg = next(rule for rule in f["rules"] if rule["field"] == "cgpa")
    assert cg["excluded"] == 4 and cg["sole"] == 4
