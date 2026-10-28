"""Standard output envelope returned by every specialist agent (CLAUDE.md rule 3)."""

from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

AgentId = Literal["M1", "M2", "M3", "M4", "M5", "M6", "M7", "MF1", "MF2", "MF3"]
NonEmpty = Field(min_length=1)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(_Model):
    id: str = NonEmpty
    kind: Literal["dataset", "filing", "api"]
    ref: str = NonEmpty  # dataset name, filing URL or API call
    retrieved_at: AwareDatetime
    providers: list[str] = []  # data_sources names behind the rows read; empty = unknown, never public


class Metric(_Model):
    id: str = NonEmpty
    name: str = NonEmpty
    value: float | str
    unit: str | None = None
    formula_id: str = NonEmpty
    source_query_id: str = NonEmpty  # must match a Source.id


class Finding(_Model):
    id: str = NonEmpty
    text: str = NonEmpty
    metric_ids: list[str] = NonEmpty


class Flag(_Model):
    severity: Literal["info", "watch", "alert"]
    reason: str = NonEmpty
    metric_ids: list[str] = []


class Confidence(_Model):
    level: Literal["high", "medium", "low"]
    reason: str = NonEmpty


class Gap(_Model):
    item: str = NonEmpty
    reason: str = NonEmpty


class Envelope(_Model):
    schema_version: Literal[1] = 1
    agent_id: AgentId
    subject: str = NonEmpty
    as_of: AwareDatetime
    metrics: list[Metric]
    findings: list[Finding]
    flags: list[Flag]
    sources: list[Source]
    confidence: Confidence
    gaps: list[Gap]

    @model_validator(mode="after")
    def _check_refs(self) -> Self:
        for label, ids in (
            ("source", [s.id for s in self.sources]),
            ("metric", [m.id for m in self.metrics]),
            ("finding", [f.id for f in self.findings]),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} ids")

        source_ids = {s.id for s in self.sources}
        for m in self.metrics:
            if m.source_query_id not in source_ids:
                raise ValueError(f"metric {m.id!r} cites unknown source {m.source_query_id!r}")

        metric_ids = {m.id for m in self.metrics}
        for owner in [*self.findings, *self.flags]:
            missing = set(owner.metric_ids) - metric_ids
            if missing:
                raise ValueError(f"{type(owner).__name__.lower()} references unknown metrics {sorted(missing)}")
        return self
