"""LLM routing and fall-back, the grounded assistant, and the SQLite -> PostgreSQL/Neon copy tool."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base, normalise_url
from app.models import DriveStatus, Student
from app.services import assistant, llm
from tests.conftest import CSRF, login, make_cell_user, make_drive, make_student


def _settings(**kw: object) -> Settings:
    base = {"azure_openai_endpoint": "", "azure_openai_api_key": "", "azure_openai_deployment": "",
            "groq_api_key": "", "llm_provider": "none", "llm_api_key": ""}
    return Settings(_env_file=None, **{**base, **kw})  # type: ignore[arg-type]


AZ = {"azure_openai_endpoint": "https://hf.openai.azure.com/", "azure_openai_api_key": "k1",
      "azure_openai_deployment": "gpt-4o-mini"}


# ---------------------------------------------------------------- database URLs
def test_neon_urls_use_psycopg3() -> None:
    url = "postgresql://u:p@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    assert normalise_url(url).startswith("postgresql+psycopg://u:p@ep-x-pooler")
    assert normalise_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalise_url("sqlite:///./data/x.db") == "sqlite:///./data/x.db"


# ---------------------------------------------------------------- routing
def test_azure_url_accepts_portal_forms() -> None:
    for e in ("https://hf.openai.azure.com", "https://hf.openai.azure.com/", "https://hf.openai.azure.com/openai/v1/"):
        assert llm.azure_url(e) == "https://hf.openai.azure.com/openai/v1/chat/completions"
    assert llm.azure_url("https://hf.services.ai.azure.com/api/projects/p1") == \
        "https://hf.services.ai.azure.com/openai/v1/chat/completions"


def test_routing_prefers_azure_for_extraction_and_groq_for_chat() -> None:
    s = _settings(groq_api_key="g", **AZ)
    assert [p.name for p in llm.route("extract", s)] == ["azure", "groq"]
    assert [p.name for p in llm.route("chat", s)] == ["groq", "azure"]
    assert llm.route("rewrite", s)[1].model == s.groq_fast_model
    forced = _settings(groq_api_key="g", llm_chat_provider="azure", **AZ)
    assert [p.name for p in llm.route("chat", forced)] == ["azure", "groq"]
    assert llm.route("chat", _settings()) == []


def test_complete_falls_back_to_the_next_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_post(p: llm.Provider, body: dict, timeout: float) -> dict:
        calls.append(p.name)
        if p.name == "azure":
            raise llm.LLMError("azure HTTP 429: quota")
        return {"choices": [{"message": {"content": " hello "}}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    out = llm.complete([{"role": "user", "content": "hi"}], "extract", settings=_settings(groq_api_key="g", **AZ))
    assert calls == ["azure", "groq"] and out.text == "hello" and out.provider.startswith("groq:")


def test_unsupported_parameters_are_dropped_and_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict] = []

    def fake_post(p: llm.Provider, body: dict, timeout: float) -> dict:
        bodies.append(body)
        if "temperature" in body:
            raise llm.LLMError("azure HTTP 400: Unsupported value: 'temperature' does not support 0.2")
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    assert llm.complete([{"role": "user", "content": "x"}], settings=_settings(**AZ)).text == "ok"
    assert "temperature" in bodies[0] and "temperature" not in bodies[1]
    assert "max_completion_tokens" in bodies[1]


def test_stream_skips_empty_azure_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = ['data: {"choices":[],"prompt_filter_results":[]}', 'data: {"choices":[{"delta":{"content":"Hel"}}]}',
             "", 'data: {"choices":[{"delta":{"content":"lo"}}]}', "data: [DONE]"]
    monkeypatch.setattr(llm, "_stream_lines", lambda p, body, timeout: iter(lines))
    label, pieces = llm.stream([{"role": "user", "content": "x"}], settings=_settings(**AZ))
    assert label == "azure:gpt-4o-mini" and "".join(pieces) == "Hello"


# ---------------------------------------------------------------- assistant
def _events(text: str) -> list[dict]:
    return [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]


def test_student_assistant_without_llm_uses_the_policy_engine(client: TestClient, db: Session) -> None:
    make_drive(db, ctc=12.0, status=DriveStatus.open, company="Chipset", cgpa_min=9.0)
    s = make_student(db, "B240001CS", cgpa=8.0)
    db.commit()
    login(client, s.email)
    r = client.post("/api/me/assistant", json={"messages": [{"role": "user", "content": "Am I eligible for Chipset?"}]},
                    headers=CSRF)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = _events(r.text)
    assert ev[0] == {"provider": "policy-engine"} and ev[-1] == {"done": True}
    text = "".join(e.get("delta", "") for e in ev)
    assert "not eligible for Chipset" in text


def test_student_context_has_no_identity_and_no_other_students(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    make_drive(db, ctc=8.0, status=DriveStatus.open, company="Acme")
    me = make_student(db, "B240002CS", cgpa=8.5)
    make_student(db, "B240003CS", cgpa=7.0)
    db.commit()
    seen: list[list[dict]] = []

    def fake_stream(messages: list[dict], task: str = "chat", **_: object) -> tuple[str, object]:
        seen.append(messages)
        return "groq:test", iter(["You are eligible for Acme (P0)."])

    monkeypatch.setattr(assistant, "get_settings", lambda: _settings(groq_api_key="g"))
    monkeypatch.setattr(llm, "stream", fake_stream)
    reply = assistant.student_reply(db, me, [{"role": "user", "content": "Which drives can I apply to?"}])
    assert reply.provider == "groq:test" and "".join(reply.pieces).startswith("You are eligible")
    system = seen[0][0]["content"]
    for secret in (me.name, me.roll_no, me.email, "B240003CS"):
        assert secret not in system
    assert "Acme" in system and '"cgpa": 8.5' in system


def test_cell_context_is_aggregate_only(db: Session) -> None:
    make_drive(db, ctc=8.0, status=DriveStatus.open, company="Acme")
    s = make_student(db, "B240004CS", cgpa=6.0)
    db.commit()
    ctx = json.dumps(assistant.cell_context(db), default=str)
    assert s.name not in ctx and s.roll_no not in ctx and s.email not in ctx
    assert '"students": 1' in ctx and "Acme" in ctx


def test_history_is_trimmed_and_must_end_with_the_user() -> None:
    msgs = [{"role": "assistant", "content": "hi"}] + [{"role": "user", "content": str(i)} for i in range(20)]
    out = assistant.clean_history(msgs)
    assert len(out) == assistant.MAX_TURNS and out[0]["role"] == "user"
    with pytest.raises(ValueError):
        assistant.clean_history([{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}])


def test_cell_llm_status_and_assistant_summary(client: TestClient, db: Session) -> None:
    make_cell_user(db)
    db.commit()
    login(client, "admin@nitc.ac.in")
    assert client.get("/api/cell/llm/status").json()["configured"] == []
    r = client.post("/api/cell/assistant", json={"messages": [{"role": "user", "content": "How is the season?"}]},
                    headers=CSRF)
    ev = _events(r.text)
    assert ev[0] == {"provider": "summary"} and "students placed" in "".join(e.get("delta", "") for e in ev)


# ---------------------------------------------------------------- database copy
def test_migrate_copies_everything_and_refuses_to_overwrite(tmp_path: Path) -> None:
    from app import models  # noqa: F401
    from app.migrate_db import migrate

    src_url, dst_url = f"sqlite:///{tmp_path / 'src.db'}", f"sqlite:///{tmp_path / 'dst.db'}"
    src = create_engine(src_url)
    Base.metadata.create_all(src)
    with Session(src) as s:
        for i in range(3):
            s.add(Student(roll_no=f"B2500{i}CS", name=f"S{i}", email=f"s{i}@nitc.ac.in", branch="CSE",
                          programme="BTECH", cgpa=8.0, grad_year=2026, skills=["python"]))
        s.commit()
    copied = migrate(src_url, dst_url, log=lambda *_: None)
    assert copied["students"] == 3
    with Session(create_engine(dst_url)) as d:
        assert d.scalar(select(func.count()).select_from(Student)) == 3
    with pytest.raises(SystemExit):
        migrate(src_url, dst_url, log=lambda *_: None)
    assert migrate(src_url, dst_url, replace=True, log=lambda *_: None)["students"] == 3
