"""LangSmith tracing: off by default, one trace per AI workflow, and no keys or identities in what is sent."""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import DriveStatus
from app.services import assistant, jd_parser, llm, qa, tracing
from tests.conftest import make_drive, make_student

ls = pytest.importorskip("langsmith")

AZ = {"azure_openai_endpoint": "https://hf.openai.azure.com/", "azure_openai_api_key": "azure-secret-key",
      "azure_openai_deployment": "gpt-4.1-mini"}
GROQ = {"groq_api_key": "groq-secret-key"}


def _settings(**kw: object) -> Settings:
    base = {"azure_openai_endpoint": "", "azure_openai_api_key": "", "azure_openai_deployment": "",
            "groq_api_key": "", "llm_provider": "none", "llm_api_key": "", "langsmith_tracing": False,
            "langsmith_api_key": "", "langsmith_agent_id": "", "langsmith_agent_environment": "",
            "langsmith_project": ""}
    return Settings(_env_file=None, **{**base, **kw})  # type: ignore[arg-type]


class Recorder(ls.Client):  # type: ignore[misc]
    """An in-memory LangSmith client: keeps every run instead of sending it."""

    def __init__(self) -> None:
        super().__init__(api_key="test", api_url="http://localhost:9", auto_batch_tracing=False)
        self.recorded: dict[str, dict[str, Any]] = {}

    def create_run(self, *args: Any, **kw: Any) -> None:
        self.recorded[str(kw["id"])] = dict(kw)

    def update_run(self, run_id: Any, **kw: Any) -> None:
        self.recorded.setdefault(str(run_id), {}).update({k: v for k, v in kw.items() if v is not None})

    def named(self, name: str) -> list[dict[str, Any]]:
        return [r for r in self.recorded.values() if r.get("name") == name]

    def one(self, name: str) -> dict[str, Any]:
        found = self.named(name)
        assert len(found) == 1, (name, [r.get("name") for r in self.recorded.values()])
        return found[0]

    def dump(self) -> str:
        return json.dumps(list(self.recorded.values()), default=str)


@pytest.fixture()
def rec() -> Iterator[Recorder]:
    r = Recorder()
    with ls.tracing_context(client=r, enabled=True, project_name="hireflo-tests"):
        yield r


def _parent(rec: Recorder, run: dict[str, Any]) -> str | None:
    pid = run.get("parent_run_id")
    return rec.recorded[str(pid)]["name"] if pid else None


# ---------------------------------------------------------------- configuration
def test_tracing_is_off_unless_switched_on_with_a_key() -> None:
    assert not _settings().langsmith_enabled
    assert not _settings(langsmith_tracing=True).langsmith_enabled  # no key
    assert not _settings(langsmith_api_key="k").langsmith_enabled   # not switched on
    assert tracing.configure(_settings()) is False
    assert tracing.status()["enabled"] is False


def test_configure_sends_to_the_agent_or_a_project() -> None:
    try:
        on = _settings(langsmith_tracing=True, langsmith_api_key="lsv2_test", langsmith_agent_id="hireflo",
                       langsmith_agent_environment="local")
        assert tracing.configure(on) is True
        assert tracing.status() == {"enabled": True, "destination": "agent hireflo (local)", "sdk": True}
        assert tracing.configure(_settings(langsmith_tracing=True, langsmith_api_key="lsv2_test")) is True
        assert tracing.status()["destination"] == "project hireflo"
    finally:
        tracing.configure(_settings())
    assert tracing.status()["enabled"] is False


def test_rejected_tracing_settings_turn_tracing_off_instead_of_crashing() -> None:
    # LangSmith only accepts local/development/staging/production; a host-specific name once stopped start-up.
    bad = _settings(langsmith_tracing=True, langsmith_api_key="lsv2_test", langsmith_agent_id="hireflo",
                    langsmith_agent_environment="render")
    assert tracing.configure(bad) is False
    assert tracing.status()["enabled"] is False


def test_status_endpoint_reports_tracing() -> None:
    assert llm.status(_settings())["tracing"]["enabled"] is False


# ---------------------------------------------------------------- LLM calls
def test_each_provider_attempt_is_an_llm_run_without_keys(rec: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(p: llm.Provider, body: dict, timeout: float) -> dict:
        if p.name == "azure":
            raise llm.LLMError("azure HTTP 429: quota")
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}}

    monkeypatch.setattr(llm, "_post", fake_post)
    out = llm.complete([{"role": "user", "content": "hello"}], "extract", settings=_settings(**GROQ, **AZ))
    assert out.text == "hi"
    failed, answered = rec.one("azure:gpt-4.1-mini"), rec.one("groq:llama-3.3-70b-versatile")
    assert failed["run_type"] == "llm" and "429" in failed["error"]
    assert answered["outputs"]["usage_metadata"] == {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}
    assert answered["inputs"]["messages"] == [{"role": "user", "content": "hello"}]
    assert answered["extra"]["metadata"]["ls_provider"] == "groq"
    assert answered["extra"]["metadata"]["ls_model_name"] == "llama-3.3-70b-versatile"
    dump = rec.dump()
    assert "azure-secret-key" not in dump and "groq-secret-key" not in dump


def test_streamed_llm_run_records_the_whole_answer(rec: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = ['data: {"choices":[{"delta":{"content":"Hel"}}]}', 'data: {"choices":[{"delta":{"content":"lo"}}]}',
             "data: [DONE]"]
    monkeypatch.setattr(llm, "_stream_lines", lambda p, body, timeout: iter(lines))
    label, pieces = llm.stream([{"role": "user", "content": "x"}], settings=_settings(**GROQ))
    assert "".join(pieces) == "Hello"
    run = rec.one(label)
    assert run["outputs"]["choices"][0]["message"]["content"] == "Hello" and run["inputs"]["stream"] is True


def test_connection_test_is_one_trace(rec: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "_post", lambda p, body, timeout: {"choices": [{"message": {"content": "OK"}}]})
    llm.test_providers(_settings(**GROQ, **AZ))
    root = rec.one("llm_connection_test")
    assert root["inputs"] == {}
    assert {_parent(rec, r) for r in rec.recorded.values() if r["run_type"] == "llm"} == {"llm_connection_test"}
    assert "secret-key" not in rec.dump()


# ---------------------------------------------------------------- workflows
def test_student_assistant_trace_spans_the_stream_and_holds_no_identity(
        rec: Recorder, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    make_drive(db, ctc=8.0, status=DriveStatus.open, company="Acme")
    me = make_student(db, "B240010CS", cgpa=8.5)
    db.commit()
    s = _settings(**GROQ)
    monkeypatch.setattr(assistant, "get_settings", lambda: s)
    monkeypatch.setattr(llm, "get_settings", lambda: s)
    lines = ['data: {"choices":[{"delta":{"content":"You can "}}]}',
             'data: {"choices":[{"delta":{"content":"apply to Acme."}}]}', "data: [DONE]"]
    monkeypatch.setattr(llm, "_stream_lines", lambda p, body, timeout: iter(lines))

    reply = assistant.student_reply(db, me, [{"role": "user", "content": "Can I apply to Acme?"}])
    assert reply.provider == "groq:llama-3.3-70b-versatile"
    assert not rec.named("assistant.student")[0].get("outputs")  # still open while the answer streams
    assert "".join(reply.pieces) == "You can apply to Acme."

    root = rec.one("assistant.student")
    assert root["inputs"]["question"] == "Can I apply to Acme?"
    assert root["outputs"] == {"provider": "groq:llama-3.3-70b-versatile", "answer": "You can apply to Acme."}
    for child in ("policy_engine", "grounding_context", "groq:llama-3.3-70b-versatile"):
        assert _parent(rec, rec.one(child)) == "assistant.student"
    dump = rec.dump()
    for secret in (me.name, me.roll_no, me.email, "groq-secret-key"):
        assert secret not in dump


def test_cell_assistant_without_llm_is_still_traced(rec: Recorder, db: Session,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    make_drive(db, ctc=8.0, status=DriveStatus.open, company="Acme")
    db.commit()
    monkeypatch.setattr(assistant, "get_settings", lambda: _settings())
    reply = assistant.cell_reply(db, [{"role": "user", "content": "How is the season?"}])
    text = "".join(reply.pieces)
    root = rec.one("assistant.cell")
    assert root["outputs"] == {"provider": "summary", "answer": text}
    assert _parent(rec, rec.one("grounding_context")) == "assistant.cell"


def test_policy_qa_trace_records_only_the_question(rec: Recorder, db: Session,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    make_drive(db, ctc=12.0, status=DriveStatus.open, company="Chipset", cgpa_min=9.0)
    me = make_student(db, "B240011CS", cgpa=8.0)
    db.commit()
    s = _settings(**GROQ)
    monkeypatch.setattr(qa, "get_settings", lambda: s)
    monkeypatch.setattr(llm, "_post", lambda p, body, timeout: {"choices": [{"message": {"content": "No."}}]})
    ans = qa.answer(db, me, "Am I eligible for Chipset?")
    assert ans.answer == "No."
    root = rec.one("policy_qa")
    assert root["inputs"] == {"question": "Am I eligible for Chipset?"}
    assert _parent(rec, rec.one("policy_engine")) == "policy_qa"
    assert _parent(rec, rec.one("groq:llama-3.1-8b-instant")) == "policy_qa"  # rewrites use the fast model
    assert me.email not in rec.dump() and me.name not in rec.dump()


def test_jd_extraction_trace_never_records_settings(rec: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"rules": [{"field": "cgpa", "op": ">=", "value": 7.5, "source_text": "CGPA 7.5 and above.",
                        "confidence": 0.9}], "skills": ["python"], "ctc_lpa": 12}
    monkeypatch.setattr(llm, "_post",
                        lambda p, body, timeout: {"choices": [{"message": {"content": json.dumps(reply)}}]})
    jd = "Software Engineer. CGPA 7.5 and above. Python. CTC 12 LPA."
    ext = jd_parser.extract(jd, _settings(**AZ))
    assert ext.extractor == "llm:azure:gpt-4.1-mini"
    root = rec.one("jd_extraction")
    assert root["inputs"] == {"jd_text": jd} and root["outputs"]["extractor"] == "llm:azure:gpt-4.1-mini"
    for child in ("pattern_extractor", "azure:gpt-4.1-mini", "parse_llm_json"):
        assert _parent(rec, rec.one(child)) == "jd_extraction"
    assert "azure-secret-key" not in rec.dump()


def test_jd_extraction_without_llm_traces_the_pattern_extractor(rec: Recorder) -> None:
    ext = jd_parser.extract("CGPA 7 and above. No backlogs.", _settings())
    assert ext.extractor == "patterns"
    assert _parent(rec, rec.one("pattern_extractor")) == "jd_extraction"


def test_nothing_is_recorded_when_tracing_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    r = Recorder()
    monkeypatch.setattr(llm, "_post", lambda p, body, timeout: {"choices": [{"message": {"content": "ok"}}]})
    with ls.tracing_context(client=r, enabled=False):
        llm.complete([{"role": "user", "content": "x"}], settings=_settings(**GROQ))
        jd_parser.extract("CGPA 7 and above.", _settings())
    assert r.recorded == {}
