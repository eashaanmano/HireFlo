"""LangSmith tracing for every AI workflow in HireFlo. Off unless configured; the app never depends on it.

Configure in backend/.env (see .env.example): ``LANGSMITH_TRACING=true``, ``LANGSMITH_API_KEY`` and a
destination, either ``LANGSMITH_AGENT_ID`` + ``LANGSMITH_AGENT_ENVIRONMENT`` (agent-based workspaces) or
``LANGSMITH_PROJECT`` (project-based and self-hosted workspaces). ``configure()`` runs at startup and hands the
SDK an explicit client, destination and switch, so nothing depends on the variables being exported to the
process, and tests can't send traces by accident.

What a trace looks like (one per user action):

* ``assistant.student`` / ``assistant.cell``: the grounding context, the policy-engine answer (students) and the
  streamed LLM call, with the full answer as the trace's output.
* ``policy_qa``: the policy engine's answer and the LLM rewrite of it.
* ``jd_extraction``: the pattern extractor, the LLM call(s) and the validated rules.
* ``llm_connection_test``: one LLM call per configured provider.

Every LLM call is an ``llm`` run named after its provider and model (``azure:gpt-4.1-mini``), with the request as
the provider received it, the reply, token usage where the provider reports it, and the error when it failed, so
fall-backs and parameter retries show up as separate, failed attempts.

What is never traced: API keys (provider objects are reduced to the request body), the database session, ORM
rows or Settings. LangSmith sees what the LLM provider sees and nothing more.
"""
from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Mapping
from typing import Any, TypeVar, cast

from ..core.config import Settings, get_settings

try:  # optional, like the openai SDK: without the package every decorator below is a no-op
    import langsmith as _ls
except ImportError:  # pragma: no cover - exercised only when the package is missing
    _ls = None  # type: ignore[assignment]

log = logging.getLogger("driveops.tracing")

F = TypeVar("F", bound=Callable[..., Any])

# Provider names as LangSmith's cost table knows them.
_LS_PROVIDER = {"azure": "azure", "groq": "groq", "anthropic": "anthropic", "openai": "openai"}

_state: dict[str, Any] = {"enabled": False, "destination": None, "client": None}


def available() -> bool:
    return _ls is not None


# ---------------------------------------------------------------- setup
def configure(settings: Settings | None = None) -> bool:
    """Point the SDK at LangSmith, or switch it off. Safe to call again; returns whether tracing is on."""
    s = settings or get_settings()
    if _ls is None:
        if s.langsmith_enabled:
            log.warning("LANGSMITH_TRACING is on but the langsmith package isn't installed; not tracing.")
        _state.update(enabled=False, destination=None, client=None)
        return False
    if not s.langsmith_enabled:
        # Explicitly off, so a LANGSMITH_TRACING variable left in the shell can't turn it on behind our back.
        _ls.configure(client=None, enabled=False, project_name=None, address=None, metadata=None)
        _state.update(enabled=False, destination=None, client=None)
        return False
    client = _ls.Client(api_key=s.langsmith_api_key, api_url=s.langsmith_endpoint or None)
    meta = {"app": "hireflo"}
    if s.langsmith_agent_id and s.langsmith_agent_environment:
        address = _ls.agent_address(s.langsmith_agent_id, s.langsmith_agent_environment)
        _ls.configure(client=client, enabled=True, address=address, metadata=meta)
        destination = f"agent {s.langsmith_agent_id} ({s.langsmith_agent_environment})"
    else:
        project = s.langsmith_project or "hireflo"
        _ls.configure(client=client, enabled=True, project_name=project, metadata=meta)
        destination = f"project {project}"
    _state.update(enabled=True, destination=destination, client=client)
    log.info("LangSmith tracing on, sending to %s", destination)
    return True


def flush() -> None:
    """Send any traces still queued (called on shutdown; the SDK batches runs in a background thread)."""
    client = _state.get("client")
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:  # noqa: BLE001 - tracing must never break shutdown
        log.warning("LangSmith flush failed: %s", str(exc)[:200])


def status() -> dict[str, Any]:
    return {"enabled": bool(_state["enabled"]), "destination": _state["destination"], "sdk": available()}


# ---------------------------------------------------------------- decorator
def traceable(**options: Any) -> Callable[[F], F]:
    """``langsmith.traceable(**options)`` when the SDK is installed; otherwise a decorator that only drops
    ``langsmith_extra``. Keeps the decorated function's type.

    Use like the real one: ``@traceable(run_type="chain", name="...", process_inputs=...)``. To name a run at call
    time, declare ``langsmith_extra: Mapping[str, Any] | None = None`` on the function and pass
    ``langsmith_extra={"name": ..., "metadata": ...}``; the decorator consumes it, so the body never sees it.
    """
    if _ls is not None:
        return cast(Callable[[F], F], _ls.traceable(**options))

    def deco(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, langsmith_extra: Any = None, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)
        return cast(F, wrapper)

    return deco


# ---------------------------------------------------------------- run payload helpers
def only(*keys: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """A ``process_inputs`` that keeps just the named arguments (drops db sessions, ORM rows and Settings)."""
    def keep(inputs: Mapping[str, Any]) -> dict[str, Any]:
        return {k: inputs[k] for k in keys if k in inputs}
    return keep


def llm_metadata(provider_name: str, style: str, model: str) -> dict[str, Any]:
    """Metadata LangSmith uses to label the model and price the tokens."""
    kind = "anthropic" if style == "anthropic" else ("openai" if provider_name == "generic" else provider_name)
    return {"ls_provider": _LS_PROVIDER.get(kind, kind), "ls_model_name": model, "ls_model_type": "chat"}


def usage_metadata(data: Mapping[str, Any]) -> dict[str, int] | None:
    """Token counts from an OpenAI-style (``usage.prompt_tokens``) or Anthropic-style (``usage.input_tokens``)
    response, in LangSmith's ``usage_metadata`` shape."""
    usage = data.get("usage") if isinstance(data, Mapping) else None
    if not isinstance(usage, Mapping):
        return None
    inp = usage.get("prompt_tokens", usage.get("input_tokens"))
    out = usage.get("completion_tokens", usage.get("output_tokens"))
    if not isinstance(inp, int) or not isinstance(out, int):
        return None
    total = usage.get("total_tokens")
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": total if isinstance(total, int) else inp + out}


def llm_outputs(data: Any) -> dict[str, Any]:
    """``process_outputs`` for a non-streamed LLM run: the provider's reply plus ``usage_metadata``."""
    if not isinstance(data, Mapping):
        return {"output": data}
    out = dict(data)
    usage = usage_metadata(data)
    if usage:
        out["usage_metadata"] = usage
    return out


def joined_message(pieces: list[Any]) -> dict[str, Any]:
    """``reduce_fn`` for a streamed LLM run: the pieces as one assistant message (OpenAI format)."""
    return {"choices": [{"message": {"role": "assistant", "content": "".join(str(p) for p in pieces)}}]}
