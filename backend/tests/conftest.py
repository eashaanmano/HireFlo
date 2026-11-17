"""Test fixtures: an isolated SQLite database per test and a FastAPI TestClient."""
from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="driveops-tests-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP / 'bootstrap.db'}")
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough"
os.environ["DEMO_MODE"] = "true"
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")
os.environ["MONITOR_INTERVAL_SEC"] = "3600"
os.environ["SOLVER_TIME_LIMIT_SEC"] = "2"
# Tests never call real LLMs, even when backend/.env holds keys.
for _k in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "GROQ_API_KEY", "LLM_API_KEY"):
    os.environ[_k] = ""
os.environ["LLM_PROVIDER"] = "none"
# ...and never send traces to LangSmith (the tracing tests use an in-memory client).
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGSMITH_API_KEY"] = ""
# Older tests schedule "from now" at any hour of the day; give them a round-the-clock day. The multi-day tests
# pass their own working hours.
os.environ["WORK_DAY_START"] = "00:00"
os.environ["WORK_DAY_END"] = "24:00"
os.environ["WORK_BREAKS"] = ""
os.environ["SCHEDULE_SKIP_SUNDAYS"] = "false"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import database  # noqa: E402
from app.core.ratelimit import limiter  # noqa: E402
from app.core.timeutil import utcnow  # noqa: E402
from app.models import (  # noqa: E402
    Company,
    Drive,
    DriveStatus,
    EligibilityRule,
    Panel,
    Resume,
    Role,
    Round,
    RoundKind,
    Student,
    User,
)
from app.services import eligibility  # noqa: E402


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture()
def db(db_url: str) -> Iterator[Session]:
    database.configure_engine(db_url)
    database.Base.metadata.create_all(bind=database.engine)
    limiter.reset()
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()
        database.Base.metadata.drop_all(bind=database.engine)


@pytest.fixture()
def client(db: Session) -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def make_student(db: Session, roll: str, *, branch: str = "CSE", cgpa: float = 8.0, backlogs: int = 0,
                 programme: str = "BTECH", skills: list[str] | None = None, with_resume: bool = True) -> Student:
    s = Student(roll_no=roll, name=f"Student {roll}", email=f"{roll.lower()}@nitc.ac.in", branch=branch,
                programme=programme, cgpa=cgpa, backlogs=backlogs, grad_year=2026, skills=skills or ["python"])
    db.add(s)
    db.flush()
    db.add(User(email=s.email, name=s.name, role=Role.student, student_id=s.id))
    if with_resume:
        upload = Path(os.environ["UPLOAD_DIR"]) / "resumes"
        upload.mkdir(parents=True, exist_ok=True)
        (upload / f"{s.id}.pdf").write_bytes(b"%PDF-1.4 test")
        db.add(Resume(student_id=s.id, track="Software", filename="r.pdf", path=f"resumes/{s.id}.pdf"))
    db.flush()
    db.refresh(s)
    return s


def make_cell_user(db: Session, role: Role = Role.admin) -> User:
    u = User(email=f"{role.value}@nitc.ac.in", name=f"Cell {role.value}", role=role)
    db.add(u)
    db.flush()
    return u


def make_drive(db: Session, *, ctc: float = 12.0, status: DriveStatus = DriveStatus.open, cgpa_min: float = 7.0,
               panels: int = 2, company: str = "Acme Systems") -> Drive:
    c = Company(name=company)
    db.add(c)
    db.flush()
    d = Drive(company_id=c.id, role="Engineer", ctc_lpa=ctc, status=status, skills=["python", "sql"],
              apply_deadline=utcnow() + timedelta(days=2), target_hires=2)
    d.rules.append(EligibilityRule(field="cgpa", op=">=", value=cgpa_min, approved=True))
    d.rules.append(EligibilityRule(field="backlogs", op="<=", value=0, approved=True))
    db.add(d)
    db.flush()
    test_round = Round(drive_id=d.id, name="Online test", kind=RoundKind.test, seq=1, duration_min=60)
    interview = Round(drive_id=d.id, name="Technical interview", kind=RoundKind.interview, seq=2, duration_min=30)
    db.add_all([test_round, interview])
    db.flush()
    for i in range(panels):
        db.add(Panel(drive_id=d.id, round_id=interview.id, name=f"Panel {chr(65 + i)}", room=f"Room {i + 1}"))
    db.flush()
    db.refresh(d)
    eligibility.recompute_drive(db, d)
    return d


def login(client: TestClient, email: str) -> None:
    r = client.post("/api/auth/otp/request", json={"email": email})
    assert r.status_code == 200, r.text
    code = r.json()["demo_code"]
    r = client.post("/api/auth/otp/verify", json={"email": email, "code": code})
    assert r.status_code == 200, r.text


CSRF = {"X-DriveOps-CSRF": "1"}
