"""L0 output (docs/contracts/entity-detection.md, v0.2). In-process contract."""

from __future__ import annotations

from pydantic import Field

from umai_decision_engine.contracts.common import ContractModel


class DetectedEntity(ContractModel):
    type: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    score: float = Field(ge=0, le=1)
    recognizer: str
    validated: bool | None = None
    value_hash: str
    placeholder: str


class EntitySummary(ContractModel):
    type: str
    count: int = Field(ge=0)
    distinct: int = Field(ge=0)


class EntityDetectionResult(ContractModel):
    entities: list[DetectedEntity] = Field(default_factory=list)
    summary: list[EntitySummary] = Field(default_factory=list)
    redacted_text: str
    language: str
    truncated: bool = False
    latency_ms: float = 0.0
