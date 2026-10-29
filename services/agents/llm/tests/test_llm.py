import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx2
import pytest
from pydantic import BaseModel, Field

from services.agents.envelope import Confidence, Envelope
from services.agents.llm import client, config
from services.agents.llm.client import (
    LLMError, OpenAIProvider, RawResponse, RecordedProvider, TransientLLMError, call_json, get_provider, strict_schema,
)
from services.agents.runlog import run_logged

FAKE_KEY = "sk-test-not-a-real-key-0000"
T = datetime(2026, 10, 29, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


class Summary(BaseModel):
    headline: str
    tone: str = Field(pattern="^(positive|neutral|negative)$")
    points: list[str] = []


class ScriptedProvider:
    """Returns (or raises) the next scripted item per call and records the prompts it was given."""

    name = "scripted"

    def __init__(self, *script):
        self.script, self.prompts = list(script), []

    def complete_json(self, model, system, user, schema_name, schema, effort):
        self.prompts.append(user)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(text=None, model="gpt-6-luna"):
    return RawResponse(text=text or json.dumps({"headline": "Results filed", "tone": "neutral", "points": ["a"]}), model=model,
                       input_tokens=1200, cached_tokens=200, output_tokens=300, reasoning_tokens=100)


# --- cost, schema ----------------------------------------------------------------------------------------

def test_cost_usd():
    # luna: (1000 x 0.10 + 200 x 0.01 + 300 x 0.50) / 1M = 252 / 1M
    assert config.cost_usd("gpt-6-luna", 1200, 200, 300) == pytest.approx(0.000252)
    # sol: (1000 x 2 + 200 x 0.10 + 300 x 10) / 1M = 5020 / 1M
    assert config.cost_usd("gpt-6.1-sol", 1200, 200, 300) == pytest.approx(0.00502)
    with pytest.raises(KeyError, match="no price"):
        config.cost_usd("gpt-unknown", 1, 0, 1)
    assert set(config.MODELS.values()) <= set(config.PRICES)


def test_strict_schema_closes_every_object():
    class Inner(BaseModel):
        note: str | None = None

    class Outer(BaseModel):
        title: str
        inner: Inner
        tags: list[str] = []

    s = strict_schema(Outer)
    assert s["additionalProperties"] is False and s["required"] == ["title", "inner", "tags"]
    inner = s["$defs"]["Inner"]
    assert inner["additionalProperties"] is False and inner["required"] == ["note"]
    assert "default" not in json.dumps(s)


# --- call_json --------------------------------------------------------------------------------------------

def test_call_json_success():
    r = call_json("specialist", "sys", "user", Summary, provider=ScriptedProvider(ok()))
    assert r.value.headline == "Results filed" and r.model == "gpt-6-luna" and r.attempts == 1
    assert (r.input_tokens, r.cached_tokens, r.output_tokens, r.reasoning_tokens) == (1200, 200, 300, 100)
    assert r.cost_usd == pytest.approx(0.000252)


def test_transient_errors_are_retried_with_backoff(monkeypatch):
    waits = []
    monkeypatch.setattr(client, "_sleep", waits.append)
    p = ScriptedProvider(TransientLLMError("429"), TransientLLMError("timeout"), ok())
    r = call_json("specialist", "sys", "user", Summary, provider=p)
    assert r.attempts == 3 and waits == [1, 2]
    with pytest.raises(LLMError, match="gave up after 4 transient errors"):
        call_json("specialist", "sys", "user", Summary, provider=ScriptedProvider(*[TransientLLMError("5xx")] * 4))


def test_invalid_output_is_repaired_once_then_fails():
    p = ScriptedProvider(ok('{"headline": "x", "tone": "bullish"}'), ok())
    r = call_json("specialist", "sys", "user", Summary, provider=p)
    assert r.attempts == 2 and "did not match the required JSON schema" in p.prompts[1] and "tone" in p.prompts[1]
    assert r.cost_usd == pytest.approx(2 * 0.000252)  # both attempts are paid for
    with pytest.raises(LLMError, match="did not match the schema after 2 attempts"):
        call_json("specialist", "sys", "user", Summary, provider=ScriptedProvider(ok("not json"), ok("{}")))


def test_permanent_errors_are_not_retried():
    p = ScriptedProvider(LLMError("openai 400: BadRequestError"), ok())
    with pytest.raises(LLMError, match="400"):
        call_json("strong", "sys", "user", Summary, provider=p)
    assert len(p.prompts) == 1
    with pytest.raises(ValueError, match="unknown tier"):
        call_json("huge", "sys", "user", Summary, provider=p)


# --- logging into agent_runs ------------------------------------------------------------------------------

def envelope():
    return Envelope(agent_id="M4", subject="ACME", as_of=T, metrics=[], findings=[], flags=[], sources=[], gaps=[],
                    confidence=Confidence(level="low", reason="test"))


def test_calls_inside_a_run_are_logged_and_costed(db_conn):
    p = ScriptedProvider(TransientLLMError("429"), ok('{"bad": 1}'), ok(), ok(model="gpt-6.1-sol"))

    def compute():
        call_json("specialist", "sys", "user", Summary, provider=p)
        call_json("strong", "sys", "user", Summary, provider=p)
        return envelope(), {}

    run_logged(db_conn, "M4", "ACME", T, compute)
    rows = db_conn.execute("SELECT model, status, attempt, cost_usd FROM llm_calls ORDER BY id").fetchall()
    assert [(m, s, a) for m, s, a, _ in rows] == [("gpt-6-luna", "error", 1), ("gpt-6-luna", "invalid_output", 2),
                                                  ("gpt-6-luna", "ok", 3), ("gpt-6.1-sol", "ok", 1)]
    total = 2 * 0.000252 + 0.00502
    (cost,) = db_conn.execute("SELECT cost_usd FROM agent_runs").fetchone()
    assert float(cost) == pytest.approx(total)


def test_failed_run_still_records_its_cost(db_conn):
    def compute():
        call_json("specialist", "sys", "user", Summary, provider=ScriptedProvider(ok("nope"), ok("nope")))
        return envelope(), {}

    with pytest.raises(LLMError):
        run_logged(db_conn, "M4", "ACME", T, compute)
    status, cost = db_conn.execute("SELECT status, cost_usd FROM agent_runs").fetchone()
    assert status == "failed" and float(cost) == pytest.approx(2 * 0.000252)


def test_calls_outside_a_run_are_not_logged(db_conn):
    call_json("specialist", "sys", "user", Summary, provider=ScriptedProvider(ok()))
    assert db_conn.execute("SELECT count(*) FROM llm_calls").fetchone() == (0,)


# --- recorded provider ------------------------------------------------------------------------------------

def test_record_then_replay(tmp_path):
    live = ScriptedProvider(ok())
    recorder = RecordedProvider(tmp_path, live=live)
    first = call_json("specialist", "sys", "user text", Summary, provider=recorder)
    (saved,) = tmp_path.glob("*.json")
    assert json.loads(saved.read_text(encoding="utf-8"))["request"]["user"] == "user text"
    replay = call_json("specialist", "sys", "user text", Summary, provider=RecordedProvider(tmp_path))
    assert replay.value == first.value and live.script == []  # replay never touched the live provider
    with pytest.raises(LLMError, match="no recorded response"):
        call_json("specialist", "sys", "different", Summary, provider=RecordedProvider(tmp_path))


def test_tests_are_offline():
    assert "OPENAI_API_KEY" not in os.environ
    assert isinstance(get_provider(), RecordedProvider) and get_provider().live is None
    with pytest.raises(LLMError, match="OPENAI_API_KEY is not set"):
        OpenAIProvider()


# --- OpenAI provider over a mocked HTTP layer -------------------------------------------------------------

def openai_body(text, status="completed", model="gpt-6-luna"):
    """Shape of a Responses API reply (per the SDK's Response type), not a captured live response."""
    return {
        "id": "resp_test", "object": "response", "created_at": 1793260800, "status": status, "model": model,
        "output": [{"type": "message", "id": "msg_test", "status": "completed", "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "usage": {"input_tokens": 1200, "input_tokens_details": {"cached_tokens": 200}, "output_tokens": 300,
                  "output_tokens_details": {"reasoning_tokens": 100}, "total_tokens": 1500},
    }


def mocked_provider(handler):
    return OpenAIProvider(api_key=FAKE_KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def test_openai_provider_request_and_usage():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return httpx2.Response(200, json=openai_body(json.dumps({"headline": "h", "tone": "neutral", "points": []})))

    r = call_json("specialist", "You restate filings.", "Summarise.", Summary, provider=mocked_provider(handler))
    body = seen["body"]
    assert seen["url"].endswith("/responses") and seen["auth"] == f"Bearer {FAKE_KEY}"
    assert body["model"] == "gpt-6-luna" and body["store"] is False and body["reasoning"] == {"effort": "low"}
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["name"] == "Summary"
    assert fmt["schema"]["additionalProperties"] is False
    assert r.value.tone == "neutral" and (r.input_tokens, r.cached_tokens, r.output_tokens, r.reasoning_tokens) == (1200, 200, 300, 100)


def test_openai_provider_error_mapping():
    def status(code):
        return mocked_provider(lambda req: httpx2.Response(code, json={"error": {"message": "x", "type": "t"}}))

    with pytest.raises(TransientLLMError):
        status(429).complete_json("gpt-6-luna", "s", "u", "Summary", {}, "low")
    with pytest.raises(TransientLLMError):
        status(503).complete_json("gpt-6-luna", "s", "u", "Summary", {}, "low")
    with pytest.raises(LLMError, match="openai 400"):
        status(400).complete_json("gpt-6-luna", "s", "u", "Summary", {}, "low")
    incomplete = mocked_provider(lambda req: httpx2.Response(200, json=openai_body("{", status="incomplete")))
    with pytest.raises(LLMError, match="max_output_tokens"):
        incomplete.complete_json("gpt-6-luna", "s", "u", "Summary", {}, "low")


def test_api_key_never_reaches_the_log(db_conn):
    provider = mocked_provider(lambda req: httpx2.Response(200, json=openai_body('{"headline": "h", "tone": "neutral", "points": []}')))
    run_logged(db_conn, "M4", "ACME", T, lambda: (call_json("specialist", "s", "u", Summary, provider=provider) and envelope(), {}))
    dump = json.dumps(db_conn.execute("SELECT row_to_json(c) FROM llm_calls c").fetchall(), default=str)
    assert FAKE_KEY not in dump and "sk-test" not in dump
