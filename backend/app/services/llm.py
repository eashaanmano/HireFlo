"""One small client for every LLM HireFlo can use, with task routing and fall-back.

Providers (configure any subset in backend/.env):
* **azure**: Azure OpenAI in Microsoft Foundry (e.g. a gpt-4.1-mini deployment), through the official ``openai``
  Python SDK on Azure's v1 API: ``OpenAI(base_url="<endpoint>/openai/v1/", api_key=...)`` with the *deployment
  name* as the model. The SDK adds retries with back-off on 429/5xx and typed errors. If the package isn't
  installed, the same v1 endpoint is called over plain HTTPS instead.
* **groq**: Groq's OpenAI-compatible API, very fast, free tier.
* **generic**: the older LLM_PROVIDER setting (Anthropic, or any OpenAI-compatible base URL).

Routing ("auto"): structured work (JD extraction, rewriting answers) prefers Azure, then Groq, then generic;
chat prefers Groq for speed, then Azure, then generic. If a provider fails, the next one is tried, so one bad key
or an exhausted free tier degrades the feature instead of breaking it.

Nothing here logs prompts or keys. Callers decide what data goes into a prompt (see services/assistant.py).
With LangSmith configured, every provider attempt is traced as an ``llm`` run (services/tracing.py): the request
body and the reply, never the provider's headers or key.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import httpx

from ..core.config import Settings, get_settings
from . import tracing

try:  # official SDK for Azure OpenAI (v1 API); optional so the app still runs without it
    import openai as _openai
except ImportError:  # pragma: no cover - exercised only when the package is missing
    _openai = None  # type: ignore[assignment]

log = logging.getLogger("driveops.llm")

Task = Literal["chat", "extract", "rewrite"]
Message = dict[str, str]
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class LLMError(Exception):
    pass


@dataclass(frozen=True)
class Provider:
    name: str                      # azure | groq | generic
    model: str
    url: str
    headers: dict[str, str] = field(hash=False, compare=False)
    style: Literal["openai", "anthropic"] = "openai"
    token_param: str = "max_tokens"

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"


@dataclass
class Completion:
    text: str
    provider: str
    ms: int = 0


def azure_url(endpoint: str) -> str:
    """Accept the endpoint as the portal shows it (with or without /openai/v1) and return the chat URL."""
    base = endpoint.strip().rstrip("/")
    if "/api/projects/" in base:  # a Foundry *project* endpoint: the model API lives on the resource host
        base = base.split("/api/projects/", 1)[0]
    for tail in ("/chat/completions", "/openai/v1", "/openai"):
        if base.endswith(tail):
            base = base[: -len(tail)]
    return f"{base}/openai/v1/chat/completions"


def azure_base_url(endpoint: str) -> str:
    """Base URL for the OpenAI SDK: ``https://<resource>.openai.azure.com/openai/v1/``."""
    return azure_url(endpoint)[: -len("chat/completions")]


def sdk_available() -> bool:
    return _openai is not None


_CLIENTS: dict[tuple[str, str], Any] = {}


def _azure_client(p: Provider) -> Any:
    """One cached SDK client per endpoint and key (the client keeps a connection pool)."""
    key = (p.url, p.headers.get("api-key", ""))
    client = _CLIENTS.get(key)
    if client is None:
        client = _openai.OpenAI(base_url=p.url[: -len("chat/completions")], api_key=key[1], max_retries=2)
        _CLIENTS.clear()  # keys rarely change; keep only the current client
        _CLIENTS[key] = client
    return client


def _sdk_error(p: Provider, exc: Exception) -> LLMError:
    """Map SDK errors onto the same "<name> HTTP <status>: <message>" shape as the HTTP path."""
    if _openai is not None and isinstance(exc, _openai.APIStatusError):
        return LLMError(f"{p.name} HTTP {exc.status_code}: {str(exc.message)[:300]}")
    if _openai is not None and isinstance(exc, _openai.APITimeoutError):
        return LLMError(f"{p.name} timed out")
    return LLMError(f"{p.name} connection failed: {str(exc)[:200]}")


def _uses_sdk(p: Provider) -> bool:
    return p.name == "azure" and _openai is not None


def providers(settings: Settings | None = None) -> dict[str, Provider]:
    s = settings or get_settings()
    out: dict[str, Provider] = {}
    if s.azure_enabled:
        out["azure"] = Provider("azure", s.azure_openai_deployment, azure_url(s.azure_openai_endpoint),
                                {"api-key": s.azure_openai_api_key}, token_param="max_completion_tokens")
    if s.groq_enabled:
        out["groq"] = Provider("groq", s.groq_model, GROQ_URL, {"Authorization": f"Bearer {s.groq_api_key}"})
    if s.generic_llm_enabled:
        if s.llm_provider == "anthropic":
            out["generic"] = Provider("generic", s.llm_model, s.llm_base_url or "https://api.anthropic.com/v1/messages",
                                      {"x-api-key": s.llm_api_key, "anthropic-version": "2023-06-01"},
                                      style="anthropic")
        else:
            base = (s.llm_base_url or "https://api.openai.com/v1").rstrip("/")
            out["generic"] = Provider("generic", s.llm_model, f"{base}/chat/completions",
                                      {"Authorization": f"Bearer {s.llm_api_key}", "api-key": s.llm_api_key})
    return out


_AUTO: dict[Task, tuple[str, ...]] = {
    "chat": ("groq", "azure", "generic"),
    "extract": ("azure", "groq", "generic"),
    "rewrite": ("azure", "groq", "generic"),
}


def route(task: Task, settings: Settings | None = None) -> list[Provider]:
    """Providers to try for a task, best first. Empty when no LLM is configured."""
    s = settings or get_settings()
    avail = providers(s)
    preferred = s.llm_chat_provider if task == "chat" else s.llm_extract_provider
    order = list(_AUTO[task])
    if preferred != "auto":
        order = [preferred] + [p for p in order if p != preferred]
    chain = [avail[n] for n in order if n in avail]
    if task == "rewrite" and s.groq_fast_model:  # short rewrites don't need the big model
        chain = [replace(p, model=s.groq_fast_model) if p.name == "groq" else p for p in chain]
    return chain


# ---------------------------------------------------------------- request building
def _body(p: Provider, messages: list[Message], max_tokens: int, temperature: float | None, json_mode: bool,
          stream: bool) -> dict[str, Any]:
    if p.style == "anthropic":
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        body: dict[str, Any] = {"model": p.model, "max_tokens": max_tokens,
                                "messages": [m for m in messages if m["role"] != "system"]}
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        return body
    body = {"model": p.model, "messages": messages, p.token_param: max_tokens}
    if temperature is not None:
        body["temperature"] = temperature
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if stream:
        body["stream"] = True
    return body


def _relax(body: dict[str, Any], error_text: str) -> dict[str, Any] | None:
    """Some models reject temperature, response_format or a token parameter. Return a safer body, or None."""
    e = error_text.lower()
    b = dict(body)
    changed = False
    if "temperature" in e and "temperature" in b:
        b.pop("temperature")
        changed = True
    if "response_format" in e and "response_format" in b:
        b.pop("response_format")
        changed = True
    for old, new in (("max_completion_tokens", "max_tokens"), ("max_tokens", "max_completion_tokens")):
        if old in e and old in b:
            b[new] = b.pop(old)
            changed = True
            break
    return b if changed else None


def _post(p: Provider, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    if _uses_sdk(p):
        try:
            resp = _azure_client(p).chat.completions.create(**body, timeout=timeout)
        except _openai.OpenAIError as exc:
            raise _sdk_error(p, exc) from exc
        return resp.model_dump()
    r = httpx.post(p.url, headers={**p.headers, "content-type": "application/json"}, json=body, timeout=timeout)
    if r.status_code >= 400:
        raise LLMError(f"{p.name} HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


# ---------------------------------------------------------------- tracing (LangSmith, see services/tracing.py)
def _request(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """An LLM run's input is the request body the provider receives, never the Provider (it holds the key)."""
    return dict(inputs.get("body") or {})


def _run(p: Provider) -> dict[str, Any]:
    """Name each LLM run after its provider and model, with the metadata LangSmith prices tokens by."""
    return {"name": p.label, "metadata": tracing.llm_metadata(p.name, p.style, p.model)}


# langsmith_extra (the run's name and metadata) is consumed by the decorator; the bodies never see it.
@tracing.traceable(run_type="llm", name="llm", process_inputs=_request, process_outputs=tracing.llm_outputs)
def _traced_post(p: Provider, body: dict[str, Any], timeout: float,
                 langsmith_extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return _post(p, body, timeout)


@tracing.traceable(run_type="llm", name="llm", process_inputs=_request, reduce_fn=tracing.joined_message)
def _traced_stream(p: Provider, body: dict[str, Any], timeout: float,
                   langsmith_extra: Mapping[str, Any] | None = None) -> Iterator[str]:
    yield from _deltas(p, _stream_lines(p, body, timeout))


def _text(p: Provider, data: dict[str, Any]) -> str:
    if p.style == "anthropic":
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    choices = data.get("choices") or []
    if not choices:
        raise LLMError(f"{p.name} returned no choices")
    msg = choices[0].get("message") or {}
    return msg.get("content") or ""


def _call(p: Provider, messages: list[Message], max_tokens: int, temperature: float | None, json_mode: bool,
          timeout: float) -> str:
    body = _body(p, messages, max_tokens, temperature, json_mode, stream=False)
    try:
        return _text(p, _traced_post(p, body, timeout, langsmith_extra=_run(p)))
    except LLMError as exc:
        relaxed = _relax(body, str(exc)) if " 400" in str(exc) else None
        if relaxed is None:
            raise
        return _text(p, _traced_post(p, relaxed, timeout, langsmith_extra=_run(p)))


def complete(messages: list[Message], task: Task = "rewrite", *, max_tokens: int = 800,
             temperature: float | None = 0.2, json_mode: bool = False,
             settings: Settings | None = None) -> Completion:
    """Run one completion on the best available provider, falling back to the next on any failure."""
    s = settings or get_settings()
    chain = route(task, s)
    if not chain:
        raise LLMError("No LLM is configured.")
    errors = []
    for p in chain:
        t0 = time.perf_counter()
        try:
            text = _call(p, messages, max_tokens, temperature, json_mode, s.llm_timeout_sec)
            return Completion(text.strip(), p.label, int((time.perf_counter() - t0) * 1000))
        except (LLMError, httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("LLM %s failed for %s: %s", p.label, task, str(exc)[:200])
            errors.append(f"{p.label}: {str(exc)[:160]}")
    raise LLMError("; ".join(errors))


# ---------------------------------------------------------------- streaming (chat)
def _stream_lines(p: Provider, body: dict[str, Any], timeout: float) -> Iterator[str]:
    if _uses_sdk(p):
        # Re-emit SDK chunks as server-sent-event lines so one parser (_deltas) serves every provider.
        try:
            chunks = _azure_client(p).chat.completions.create(**body, timeout=timeout)
            for chunk in chunks:
                yield "data: " + chunk.model_dump_json()
        except _openai.OpenAIError as exc:
            raise _sdk_error(p, exc) from exc
        yield "data: [DONE]"
        return
    with httpx.stream("POST", p.url, headers={**p.headers, "content-type": "application/json"}, json=body,
                      timeout=timeout) as r:
        if r.status_code >= 400:
            r.read()
            raise LLMError(f"{p.name} HTTP {r.status_code}: {r.text[:300]}")
        yield from r.iter_lines()


def _deltas(p: Provider, lines: Iterator[str]) -> Iterator[str]:
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        for ch in data.get("choices") or []:  # Azure sends an empty first chunk with content-filter results
            piece = (ch.get("delta") or {}).get("content")
            if piece:
                yield piece


def stream(messages: list[Message], task: Task = "chat", *, max_tokens: int = 700, temperature: float | None = 0.3,
           settings: Settings | None = None) -> tuple[str, Iterator[str]]:
    """Return (provider label, iterator of text pieces). Falls back to the next provider if one fails before its
    first piece; a provider that fails mid-answer ends the answer early instead of repeating it elsewhere."""
    s = settings or get_settings()
    chain = route(task, s)
    if not chain:
        raise LLMError("No LLM is configured.")
    errors = []
    for p in chain:
        if p.style == "anthropic":
            try:
                text = _call(p, messages, max_tokens, temperature, False, s.llm_timeout_sec)
                return p.label, iter([text])
            except (LLMError, httpx.HTTPError, ValueError, KeyError) as exc:
                errors.append(f"{p.label}: {str(exc)[:160]}")
                continue
        body = _body(p, messages, max_tokens, temperature, False, stream=True)
        try:
            gen = _traced_stream(p, body, s.llm_timeout_sec, langsmith_extra=_run(p))
            first = next(gen, None)
        except LLMError as exc:
            relaxed = _relax(body, str(exc)) if " 400" in str(exc) else None
            try:
                if relaxed is None:
                    raise
                gen = _traced_stream(p, relaxed, s.llm_timeout_sec, langsmith_extra=_run(p))
                first = next(gen, None)
            except (LLMError, httpx.HTTPError) as exc2:
                log.warning("LLM stream %s failed: %s", p.label, str(exc2)[:200])
                errors.append(f"{p.label}: {str(exc2)[:160]}")
                continue
        except httpx.HTTPError as exc:
            log.warning("LLM stream %s failed: %s", p.label, str(exc)[:200])
            errors.append(f"{p.label}: {str(exc)[:160]}")
            continue
        if first is None:
            errors.append(f"{p.label}: empty answer")
            continue

        def _rest(first: str = first, gen: Iterator[str] = gen, label: str = p.label) -> Iterator[str]:
            yield first
            try:
                yield from gen
            except (LLMError, httpx.HTTPError) as exc:
                log.warning("LLM stream %s broke mid-answer: %s", label, str(exc)[:200])
                yield " …(the answer was cut off; please ask again)"
        return p.label, _rest()
    raise LLMError("; ".join(errors))


# ---------------------------------------------------------------- status and connection test
def status(settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    avail = providers(s)
    return {
        "configured": [{"name": p.name, "model": p.model, "client": "openai-sdk" if _uses_sdk(p) else "https"}
                       for p in avail.values()],
        "routing": {t: [p.label for p in route(t, s)] for t in _AUTO},
        "assistant_enabled": s.assistant_enabled,
        "tracing": tracing.status(),
    }


@tracing.traceable(run_type="chain", name="llm_connection_test", process_inputs=tracing.only())
def test_providers(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Ask each configured provider for a one-word reply. Errors are shortened and never include keys."""
    s = settings or get_settings()
    out = []
    for p in providers(s).values():
        t0 = time.perf_counter()
        try:
            text = _call(p, [{"role": "user", "content": "Reply with the single word OK."}], 16, 0.0, False,
                         min(s.llm_timeout_sec, 20))
            out.append({"provider": p.label, "ok": True, "ms": int((time.perf_counter() - t0) * 1000),
                        "reply": text.strip()[:40]})
        except (LLMError, httpx.HTTPError, ValueError, KeyError) as exc:
            msg = str(exc)
            for secret in (s.azure_openai_api_key, s.groq_api_key, s.llm_api_key):
                if secret:
                    msg = msg.replace(secret, "***")
            out.append({"provider": p.label, "ok": False, "ms": int((time.perf_counter() - t0) * 1000),
                        "error": msg[:240]})
    return out
