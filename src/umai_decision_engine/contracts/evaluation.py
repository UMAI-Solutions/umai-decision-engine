"""Evaluation API (docs/contracts/evaluation-api.md; version in ``CONTRACT_VERSION``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from umai_decision_engine.contracts.bundle import PolicyBundle
from umai_decision_engine.contracts.common import (
    Action,
    ContractModel,
    DestinationClass,
    Layer,
    Mode,
    Phase,
    Severity,
)
from umai_decision_engine.contracts.entities import EntitySummary

CONTRACT_VERSION = "0.11"
_ACCEPTED_REQUEST_VERSIONS = Literal[
    "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "0.10", "0.11"
]

# ------------------------------------------------------------------ request


class ChatMessage(ContractModel):
    role: Literal["system", "user", "assistant"]
    content: str


class Artifact(ContractModel):
    artifact_type: Literal[
        "TOOL_INPUT", "TOOL_OUTPUT", "MCP_REQUEST", "MCP_RESPONSE", "MEMORY_WRITE", "CUSTOM"
    ] = "CUSTOM"
    name: str | None = None
    payload_summary: str | None = None
    content: str | None = None
    content_type: Literal["text", "markdown", "json"] = "text"
    metadata: dict[str, Any] = Field(default_factory=dict)


class Content(ContractModel):
    messages: list[ChatMessage] = Field(min_length=1)
    focus: Literal["LAST_USER_MESSAGE", "LAST_ASSISTANT_MESSAGE"]
    artifacts: list[Artifact] = Field(default_factory=list)
    content_type: Literal["text", "markdown", "json"] = "text"
    language: str | None = Field(None, max_length=8)


class Subject(ContractModel):
    user_id: str | None = None
    role: str | None = None
    department: str | None = None
    agent_id: str | None = None
    session_id: str | None = None


class Destination(ContractModel):
    app: str | None = None
    class_: DestinationClass = Field(DestinationClass.UNKNOWN, alias="class")


class AgentAction(ContractModel):
    name: str | None = None
    tool_name: str | None = None
    server_name: str | None = None
    method: str | None = None
    memory_scope: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BundleRef(ContractModel):
    id: str = Field(min_length=1)
    version: int | None = Field(None, ge=1)  # None → the bundle's active version


class Flags(ContractModel):
    allow_model_calls: bool = True
    trace: bool = False


class EvaluationRequest(ContractModel):
    contract_version: _ACCEPTED_REQUEST_VERSIONS
    request_id: str = Field(min_length=1, max_length=128)
    timestamp: datetime
    # 0.6: optional; the credential's tenant is authoritative and fills it (ADR-0023 §5).
    # Required when the caller uses the shared operator token (self-hosted, ADR-0016).
    tenant_id: str | None = Field(None, min_length=1, max_length=128)
    # 0.10: optional for a key that belongs to an application, whose guardrail applies; required
    # otherwise (ADR-0039 §2).
    bundle: BundleRef | None = None
    bundle_inline: PolicyBundle | None = None
    phase: Phase
    content: Content
    subject: Subject = Field(default_factory=Subject)
    destination: Destination = Field(default_factory=Destination)
    action: AgentAction | None = None
    timeout_ms: int | None = Field(None, ge=50, le=60_000)
    flags: Flags = Field(default_factory=Flags)


# ------------------------------------------------------------------ response


class MatchedRule(ContractModel):
    """What produced (part of) a decision: a guardrail policy, the legacy per-type action, an
    L0 exception or an L1a rule. Ids and versions only, never content."""

    kind: Literal["l0_policy", "l0_on_detect", "l0_exception", "l1a_rule"]
    id: str
    version: int | None = None
    name: str | None = None
    action: Action
    entities: list[str] = Field(default_factory=list)


class Decision(ContractModel):
    action: Action
    allowed: bool
    severity: Severity
    reason: str
    decided_by: Literal["L0", "L1a", "L1b", "pipeline"]
    # Strictest first; empty when no rule applied (e.g. failure policy, nothing detected).
    matched: list[MatchedRule] = Field(default_factory=list)


class Tag(ContractModel):
    code: str
    layer: Layer
    score: float | None = None
    probability: float | None = None
    policy_id: str | None = None
    policy_version: int | None = None
    rule_id: str | None = None


class OutputModifications(ContractModel):
    redacted_text: str
    entities: list[str]


class BundleInfo(ContractModel):
    id: str
    version: int | None = None
    mode: Mode | None = None
    sha256: str | None = None


class LatencyMs(ContractModel):
    total: float
    l0: float | None = None
    l1a: float | None = None
    l1b: float | None = None


class TraceEntry(ContractModel):
    layer: Layer
    outcome: str
    latency_ms: float
    provider: str | None = None
    model: str | None = None
    prompt: str | None = None


class ErrorInfo(ContractModel):
    layer: Layer
    type: str
    message: str | None = None
    retryable: bool | None = None


class EvaluationResponse(ContractModel):
    contract_version: Literal[
        "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "0.10", "0.11"
    ] = CONTRACT_VERSION
    request_id: str
    tenant_id: str
    bundle: BundleInfo
    phase: Phase
    decision: Decision
    would_have: Decision | None = None
    degraded: bool = False
    tags: list[Tag] = Field(default_factory=list)
    entities: list[EntitySummary] = Field(default_factory=list)
    output_modifications: OutputModifications | None = None
    event_id: str
    review_queued: bool = False
    latency_ms: LatencyMs
    trace: list[TraceEntry] | None = None
    errors: list[ErrorInfo] = Field(default_factory=list)


# ------------------------------------------------------------------ development endpoints


class DetectedEntityView(ContractModel):
    """``/v1/detect`` view of an entity: offsets and placeholder, never the value or hash."""

    type: str
    start: int
    end: int
    score: float
    validated: bool | None = None
    placeholder: str


class DetectResponse(ContractModel):
    request_id: str
    bundle: BundleInfo
    language: str
    entities: list[DetectedEntityView]
    summary: list[EntitySummary]
    redacted_text: str
    truncated: bool
    latency_ms: float


# ------------------------------------------------------------------ errors


class ValidationProblem(ContractModel):
    """Where a malformed request is malformed and how; never the value (0.8, ADR-0005)."""

    loc: list[str | int]
    type: str


class ValidationErrorDetail(ContractModel):
    error: Literal["invalid_request"] = "invalid_request"
    detail: str
    problems: list[ValidationProblem] = Field(max_length=50)


class ValidationErrorBody(ContractModel):
    """HTTP 422 body (evaluation API 0.8): the admission error shape plus `problems`."""

    detail: ValidationErrorDetail


def input_size(content: Content) -> int:
    """Prompt size (0.9, ADR-0019 §4 and amendment): characters (Unicode code points) over all
    request content L0 can scan: every message and every artifact's ``payload_summary`` and
    ``content`` (the parts ``l0/target.py`` reads)."""

    return sum(len(m.content) for m in content.messages) + sum(
        len(a.payload_summary or "") + len(a.content or "") for a in content.artifacts
    )


class InputTooLargeDetail(ContractModel):
    error: Literal["input_too_large"] = "input_too_large"
    detail: str
    limit: int
    size: int
    unit: Literal["characters"] = "characters"


class InputTooLargeBody(ContractModel):
    """HTTP 413 body (evaluation API 0.9): nothing was evaluated, nothing was truncated."""

    detail: InputTooLargeDetail
