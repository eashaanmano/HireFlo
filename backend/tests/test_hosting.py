"""Behaviour that matters on a free host (Render + Neon): a wiped disk, an unset variable, one container."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Resume
from app.services.resumes import seed_resume_path
from tests.conftest import login, make_student


def test_seeded_resume_is_redrawn_when_the_disk_lost_it(client: TestClient, db: Session) -> None:
    s = make_student(db, "B230001CS", with_resume=False)
    rel = seed_resume_path(s.id)
    r = Resume(student_id=s.id, track="Software", filename="resume.pdf", path=str(rel))
    db.add(r)
    db.commit()
    file = Path(os.environ["UPLOAD_DIR"]) / rel
    file.unlink(missing_ok=True)

    login(client, s.email)
    res = client.get(f"/api/me/resumes/{r.id}")
    assert res.status_code == 200, res.text
    assert res.content.startswith(b"%PDF")
    assert file.is_file()


def test_lost_uploaded_resume_is_a_404_not_a_crash(client: TestClient, db: Session) -> None:
    s = make_student(db, "B230002CS")
    r = s.resumes[0]
    db.commit()
    (Path(os.environ["UPLOAD_DIR"]) / r.path).unlink()

    login(client, s.email)
    res = client.get(f"/api/me/resumes/{r.id}")
    assert res.status_code == 404
    assert "no longer on the server" in res.json()["detail"]


def test_blank_database_url_falls_back_to_sqlite() -> None:
    # A host's form can leave a variable present but empty; that must not break start-up.
    assert Settings(database_url="  ").database_url.startswith("sqlite")


def test_email_only_to_sends_to_listed_inboxes_only(monkeypatch) -> None:
    from app.services import notifications

    sent: list[str] = []
    settings = Settings(smtp_host="smtp.example.test", email_only_to=" Me_M260351CS@nitc.ac.in , ")
    monkeypatch.setattr(notifications, "get_settings", lambda: settings)
    monkeypatch.setattr(notifications, "_send_email_sync", lambda to, subject, body: sent.append(to))
    monkeypatch.setattr(notifications.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())

    assert notifications.send_email("me_m260351cs@nitc.ac.in", "Code", "123456") is True
    assert notifications.send_email("b220070cs@nitc.ac.in", "Code", "123456") is False
    assert sent == ["me_m260351cs@nitc.ac.in"]


def test_unsent_otp_is_printed_to_the_server_log(client: TestClient, db: Session, capsys) -> None:
    s = make_student(db, "B230003CS")
    db.commit()
    r = client.post("/api/auth/otp/request", json={"email": s.email})
    assert r.status_code == 200
    assert f"OTP for {s.email}: {r.json()['demo_code']}" in capsys.readouterr().out
