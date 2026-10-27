import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.agents.envelope import Envelope

SCHEMA_PATH = Path(__file__).parents[3] / "packages/shared/envelope.schema.json"


def valid() -> dict:
    return {
        "agent_id": "M1",
        "subject": "RELIANCE",
        "as_of": "2026-10-27T18:30:00+05:30",
        "metrics": [
            {"id": "rsi14", "name": "RSI (14)", "value": 61.2, "formula_id": "rsi_wilder_14", "source_query_id": "bhav"},
        ],
        "findings": [{"id": "f1", "text": "RSI is above 60.", "metric_ids": ["rsi14"]}],
        "flags": [{"severity": "info", "reason": "Momentum firm", "metric_ids": ["rsi14"]}],
        "sources": [
            {"id": "bhav", "kind": "dataset", "ref": "nse_bhavcopy_eq", "retrieved_at": "2026-10-27T18:00:00+05:30"},
        ],
        "confidence": {"level": "high", "reason": "Full 10y history"},
        "gaps": [{"item": "index series", "reason": "Nifty file missing for 2026-10-27"}],
    }


def test_valid_round_trip():
    env = Envelope.model_validate(valid())
    assert Envelope.model_validate_json(env.model_dump_json()) == env


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d["metrics"][0].update(source_query_id="nope"), id="unknown-source"),
        pytest.param(lambda d: d["findings"][0].update(metric_ids=["nope"]), id="finding-unknown-metric"),
        pytest.param(lambda d: d["findings"][0].update(metric_ids=[]), id="finding-no-metric"),
        pytest.param(lambda d: d["flags"][0].update(metric_ids=["nope"]), id="flag-unknown-metric"),
        pytest.param(lambda d: d["flags"][0].update(severity="critical"), id="bad-severity"),
        pytest.param(lambda d: d["metrics"].append(dict(d["metrics"][0])), id="duplicate-metric"),
        pytest.param(lambda d: d.update(as_of="2026-10-27T18:30:00"), id="naive-datetime"),
        pytest.param(lambda d: d.update(agent_id="F1"), id="non-phase1-agent"),
        pytest.param(lambda d: d.update(advice="buy"), id="extra-field"),
        pytest.param(lambda d: d["confidence"].update(reason=""), id="empty-confidence-reason"),
    ],
)
def test_rejects(mutate):
    data = valid()
    mutate(data)
    with pytest.raises(ValidationError):
        Envelope.model_validate(data)


def test_shared_schema_in_sync():
    # Regenerate: uv run python -m services.agents.export_schema
    assert json.loads(SCHEMA_PATH.read_text()) == Envelope.model_json_schema()
