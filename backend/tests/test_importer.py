from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Student, User
from app.services.importer import import_students, map_headers

SHEET = """Roll Number,Student Name,Institute Email,Department,Degree,CPI,Active Backlogs,Batch,Key Skills
B220001CS,asha  nair,Asha.B220001CS@nitc.ac.in,CSE,B.Tech,8.40,0,2026,Python; SQL
B220002EC,Ravi Menon,ravi@nitc.ac.in,EC,BTECH,7.9,1,2026,verilog
B220002EC,Dup Row,dup@nitc.ac.in,ECE,BTECH,7.0,0,2026,
B220003ME,No Mail,,ME,BTECH,7.1,0,2026,
B220004ME,Bad Cgpa,bad@nitc.ac.in,ME,BTECH,"8,4",0,2026,
B220005XX,Odd Branch,odd@nitc.ac.in,Mech Engg,BTECH,7.0,0,2026,
"""


def test_header_mapping_is_loose() -> None:
    m = map_headers(["Roll Number", "Student Name", "Institute Email", "CPI", "Batch", "Department", "Degree"])
    assert m["roll_no"] == "Roll Number" and m["cgpa"] == "CPI" and m["grad_year"] == "Batch"


def test_import_reports_bad_rows_and_creates_good_ones(db: Session) -> None:
    report = import_students(db, SHEET)
    assert report.created == 2
    problems = {e.row: " ".join(e.problems) for e in report.errors}
    assert "duplicate roll number" in problems[4]
    assert "missing email" in problems[5]
    assert "not a number" in problems[6]
    assert "unknown branch" in problems[7]
    asha = db.scalar(select(Student).where(Student.roll_no == "B220001CS"))
    assert asha is not None and asha.name == "Asha Nair" and asha.branch == "CSE" and asha.programme == "BTECH"
    assert asha.skills == ["python", "sql"]
    assert db.scalar(select(User).where(User.email == "asha.b220001cs@nitc.ac.in")) is not None


def test_reimport_updates_and_dry_run_writes_nothing(db: Session) -> None:
    import_students(db, SHEET)
    changed = SHEET.replace("7.9,1", "8.1,0")
    dry = import_students(db, changed, dry_run=True)
    assert dry.updated == 1 and dry.unchanged == 1
    ravi = db.scalar(select(Student).where(Student.roll_no == "B220002EC"))
    assert ravi is not None and ravi.cgpa == 7.9
    real = import_students(db, changed)
    assert real.updated == 1
    assert ravi.cgpa == 8.1 and ravi.backlogs == 0


def test_missing_required_column(db: Session) -> None:
    report = import_students(db, "Name,Email\nA,a@b.com\n")
    assert report.created == 0 and "missing column" in report.errors[0].problems[0]
