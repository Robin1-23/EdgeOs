"""Provider-agnostic structured LLM calls. Agents call call_json(); nothing else talks to a provider.

LLM output is explanation only (CLAUDE.md rule 1): schemas should hold text and choices, never numbers shown
to users. Calls made inside services.agents.runlog.run_logged are logged to llm_calls and summed into
agent_runs.cost_usd.
Providers (LLM_PROVIDER env): openai (default), recorded (replay saved responses; tests), record (replay, and
call OpenAI + save when a recording is missing; run manually only).
"""

import hashlib
import json
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Protocol, TypeVar

import psycopg
from pydantic import BaseModel, ValidationError

from services.agents.llm import config

T = TypeVar("T", bound=BaseModel)
RECORDINGS_DIR = Path(os.environ.get("LLM_RECORDINGS", Path(__file__).parent / "recordings"))

# (connection, agent_runs.id) of the agent run in progress; set by runlog.run_logged
current_run: ContextVar[tuple[psycopg.Connection, int] | None] = ContextVar("current_run", default=None)
_sleep = time.sleep  # patched in tests


class LLMError(Exception):
    """The call could not produce a valid result; the agent run fails rather than passing on a guess."""


class TransientLLMError(Exception):
    """Rate limit, timeout, connection or 5xx: worth retrying."""


@dataclass(frozen=True)
class RawResponse:
    text: str
    model: str
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    value: T
    model: str
    tier: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_usd: float
    latency_ms: int
    attempts: int


class Provider(Protocol):
    name: str

    def complete_json(self, model: str, system: str, user: str, schema_name: str, schema: dict, effort: str) -> RawResponse:
        """Return the model's JSON text and token usage, or raise TransientLLMError / LLMError."""


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema adapted to strict structured outputs: every object closed (additionalProperties
    false) with all properties required; defaults dropped."""
    schema = model.model_json_schema()

    def fix(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)

    fix(schema)
    return schema


def request_hash(model: str, system: str, user: str, schema_name: str, schema: dict, effort: str) -> str:
    payload = json.dumps({"model": model, "system": system, "user": user, "schema_name": schema_name, "schema": schema,
                          "effort": effort}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- providers ------------------------------------------------------------------------------------------

class OpenAIProvider:
    """OpenAI Responses API with strict json_schema output. The SDK's own retries are off; call_json retries."""

    name = "openai"

    def __init__(self, api_key: str | None = None, http_client=None):
        from openai import OpenAI

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY is not set (put it in the repo-root .env)")
        self.client = OpenAI(api_key=key, http_client=http_client, max_retries=0, timeout=config.TIMEOUT_SECONDS)

    def complete_json(self, model: str, system: str, user: str, schema_name: str, schema: dict, effort: str) -> RawResponse:
        import openai

        try:
            r = self.client.responses.create(
                model=model, instructions=system, input=user, reasoning={"effort": effort}, store=False,
                text={"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
            )
        except (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError) as e:
            raise TransientLLMError(f"{type(e).__name__}: {e}") from e
        except openai.APIStatusError as e:
            raise LLMError(f"openai {e.status_code}: {type(e).__name__}") from e
        if r.status != "completed":
            reason = r.incomplete_details.reason if r.incomplete_details else r.status
            raise LLMError(f"openai response not completed: {reason}")
        u = r.usage
        return RawResponse(
            text=r.output_text, model=r.model, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
            cached_tokens=(u.input_tokens_details.cached_tokens or 0) if u.input_tokens_details else 0,
            reasoning_tokens=(u.output_tokens_details.reasoning_tokens or 0) if u.output_tokens_details else 0,
        )


class RecordedProvider:
    """Replays saved responses from <dir>/<request_hash>.json. A missing recording is an error, unless `live`
    is given (record mode), in which case the live provider is called once and its response saved."""

    name = "recorded"

    def __init__(self, directory: Path = RECORDINGS_DIR, live: Provider | None = None):
        self.directory, self.live = Path(directory), live

    def complete_json(self, model: str, system: str, user: str, schema_name: str, schema: dict, effort: str) -> RawResponse:
        h = request_hash(model, system, user, schema_name, schema, effort)
        path = self.directory / f"{h}.json"
        if path.exists():
            return RawResponse(**json.loads(path.read_text(encoding="utf-8"))["response"])
        if self.live is None:
            raise LLMError(f"no recorded response {h[:12]} for {schema_name} ({model}); record with LLM_PROVIDER=record")
        raw = self.live.complete_json(model, system, user, schema_name, schema, effort)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"request": {"model": model, "schema_name": schema_name, "effort": effort,
                                                "system": system, "user": user},
                                    "response": raw.__dict__}, indent=2, ensure_ascii=False), encoding="utf-8")
        return raw


_provider: Provider | None = None


def get_provider() -> Provider:
    global _provider
    if _provider is None:
        kind = os.environ.get("LLM_PROVIDER", config.PROVIDER)
        _provider = {"openai": lambda: OpenAIProvider(), "recorded": lambda: RecordedProvider(),
                     "record": lambda: RecordedProvider(live=OpenAIProvider())}[kind]()
    return _provider


def reset_provider() -> None:
    global _provider
    _provider = None


# --- call_json ------------------------------------------------------------------------------------------

def _log(provider: Provider, model: str, tier: str, schema_name: str, h: str, attempt: int, status: str,
         raw: RawResponse | None, cost: float, latency_ms: int, error: str | None) -> None:
    run = current_run.get()
    if run is None:
        return
    conn, run_id = run
    conn.execute(
        """INSERT INTO llm_calls (run_id, provider, model, tier, schema_name, request_hash, attempt, status, input_tokens,
                                  cached_tokens, output_tokens, reasoning_tokens, cost_usd, latency_ms, error)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (run_id, provider.name, model, tier, schema_name, h, attempt, status, raw.input_tokens if raw else 0,
         raw.cached_tokens if raw else 0, raw.output_tokens if raw else 0, raw.reasoning_tokens if raw else 0,
         cost, latency_ms, error),
    )


def call_json(tier: str, system: str, user: str, schema: type[T], *, provider: Provider | None = None,
              schema_name: str | None = None) -> LLMResult[T]:
    """One structured call: the reply must validate against `schema`. Transient errors are retried with
    backoff; output that does not validate is re-asked once with the error; then LLMError."""
    if tier not in config.MODELS:
        raise ValueError(f"unknown tier {tier!r}; choose from {sorted(config.MODELS)}")
    provider = provider or get_provider()
    model, effort = config.MODELS[tier], config.REASONING_EFFORT[tier]
    name, js = schema_name or schema.__name__, strict_schema(schema)
    prompt, attempt, transient, repairs = user, 0, 0, 0
    totals = {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cost": 0.0, "ms": 0}

    while True:
        attempt += 1
        h = request_hash(model, system, prompt, name, js, effort)
        started = time.monotonic()
        try:
            raw = provider.complete_json(model, system, prompt, name, js, effort)
        except TransientLLMError as e:
            ms = int((time.monotonic() - started) * 1000)
            _log(provider, model, tier, name, h, attempt, "error", None, 0.0, ms, str(e))
            transient += 1
            if transient > config.MAX_TRANSIENT_RETRIES:
                raise LLMError(f"{name}: gave up after {transient} transient errors: {e}") from e
            _sleep(2 ** (transient - 1))
            continue
        except LLMError as e:
            _log(provider, model, tier, name, h, attempt, "error", None, 0.0, int((time.monotonic() - started) * 1000), str(e))
            raise
        ms = int((time.monotonic() - started) * 1000)
        cost = config.cost_usd(model, raw.input_tokens, raw.cached_tokens, raw.output_tokens)
        for k in ("input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens"):
            totals[k] += getattr(raw, k)
        totals["cost"] += cost
        totals["ms"] += ms
        try:
            value = schema.model_validate_json(raw.text)
        except ValidationError as e:
            errors = "; ".join(f"{'.'.join(map(str, x['loc'])) or '(root)'}: {x['msg']}" for x in e.errors())[:1000]
            _log(provider, model, tier, name, h, attempt, "invalid_output", raw, cost, ms, errors)
            if repairs >= config.MAX_REPAIR_ATTEMPTS:
                raise LLMError(f"{name}: output did not match the schema after {repairs + 1} attempts: {errors}") from e
            repairs += 1
            prompt = (f"{user}\n\nYour previous reply did not match the required JSON schema ({errors}). "
                      "Reply again with JSON that matches the schema exactly.")
            continue
        _log(provider, model, tier, name, h, attempt, "ok", raw, cost, ms, None)
        return LLMResult(value=value, model=model, tier=tier, attempts=attempt, cost_usd=totals["cost"],
                         latency_ms=totals["ms"], input_tokens=totals["input_tokens"], cached_tokens=totals["cached_tokens"],
                         output_tokens=totals["output_tokens"], reasoning_tokens=totals["reasoning_tokens"])
