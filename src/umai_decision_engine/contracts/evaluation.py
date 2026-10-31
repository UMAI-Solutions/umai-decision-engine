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

# The request and response shape last changed in 0.12; 0.13 and 0.14 added refusals only
# (docs/contracts/evaluation-api.md), so a request may name any of them.
CONTRACT_VERSION = "0.12"
_ACCEPTED_REQUEST_VERSIONS = Literal[
    "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "0.10", "0.11", "0.12", "0.13", "0.14"
]

# ------------------------------------------------------------------ request


class ChatMessage(ContractModel):
    role: Literal["system", "user", "assistant"] = Field(description="Who wrote the message.")
    content: str = Field(description="The message text.")


class Artifact(ContractModel):
    artifact_type: Literal[
        "TOOL_INPUT", "TOOL_OUTPUT", "MCP_REQUEST", "MCP_RESPONSE", "MEMORY_WRITE", "CUSTOM"
    ] = Field("CUSTOM", description="What the artifact is: a tool call, a tool result, ...")
    name: str | None = Field(None, description="The artifact's name, e.g. a file or tool name.")
    payload_summary: str | None = Field(
        None, description="A short text summary of the artifact; scanned like content."
    )
    content: str | None = Field(None, description="The artifact's text; scanned.")
    content_type: Literal["text", "markdown", "json"] = Field(
        "text", description="Format of `content`."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Your own key-value pairs; never interpreted."
    )


class Content(ContractModel):
    messages: list[ChatMessage] = Field(
        min_length=1,
        description="The conversation, oldest first. The guardrail decides which part is "
        "scanned; by default the last message.",
    )
    focus: Literal["LAST_USER_MESSAGE", "LAST_ASSISTANT_MESSAGE"] = Field(
        description="The message being checked: the prompt (`LAST_USER_MESSAGE`) or the "
        "model's answer (`LAST_ASSISTANT_MESSAGE`)."
    )
    artifacts: list[Artifact] = Field(
        default_factory=list, description="Tool calls, tool results, files or memory writes."
    )
    content_type: Literal["text", "markdown", "json"] = Field(
        "text", description="Format of the messages."
    )
    language: str | None = Field(
        None,
        max_length=8,
        description="ISO 639-1 language hint, e.g. `tr` or `en`; detected when absent.",
    )


class Subject(ContractModel):
    user_id: str | None = Field(
        None, description="Your id for the person; use a pseudonym, not an e-mail address."
    )
    role: str | None = Field(None, description="The person's role, e.g. `sales`.")
    department: str | None = Field(None, description="The person's department.")
    agent_id: str | None = Field(None, description="Your id for the AI agent acting.")
    session_id: str | None = Field(None, description="Your id for the conversation.")


class Destination(ContractModel):
    app: str | None = Field(None, description="Where the content goes, e.g. `chatgpt.com`.")
    class_: DestinationClass = Field(
        DestinationClass.UNKNOWN, alias="class", description="The kind of destination."
    )


class AgentAction(ContractModel):
    name: str | None = Field(
        None,
        description="The action's name. Required for `TOOL_INPUT`, `MCP_REQUEST` and "
        "`MEMORY_WRITE`.",
    )
    tool_name: str | None = Field(None, description="The tool called; required for `TOOL_INPUT`.")
    server_name: str | None = Field(None, description="The MCP server; required for `MCP_REQUEST`.")
    method: str | None = Field(None, description="The MCP method; required for `MCP_REQUEST`.")
    memory_scope: str | None = Field(
        None, description="The memory written to; required for `MEMORY_WRITE`."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Your own key-value pairs; never interpreted."
    )


class BundleRef(ContractModel):
    id: str = Field(min_length=1, description="The guardrail's id.")
    version: int | None = Field(  # None → the bundle's active version
        None,
        ge=1,
        description="A published version; omitted: the active version. Not allowed with an "
        "application's key.",
    )


class Flags(ContractModel):
    allow_model_calls: bool = Field(
        True,
        description="`false` skips decision-model policies (faster); the answer is then "
        "`degraded` if one applied.",
    )
    trace: bool = Field(False, description="Include the per-layer `trace` in the answer.")


class EvaluationRequest(ContractModel):
    contract_version: _ACCEPTED_REQUEST_VERSIONS = Field(
        description=f"Version of the request shape; send `{CONTRACT_VERSION}`."
    )
    request_id: str = Field(
        min_length=1, max_length=128, description="Your id for this call; echoed in the answer."
    )
    timestamp: datetime = Field(description="When you made the call (RFC 3339).")
    # 0.6: optional; the credential's tenant is authoritative and fills it (ADR-0023 §5).
    # Required when the caller uses the shared operator token (self-hosted, ADR-0016).
    tenant_id: str | None = Field(
        None,
        min_length=1,
        max_length=128,
        description="Leave it out: the key says whose request it is. If sent, it must be the "
        "key's organization.",
    )
    # 0.10: optional for a key that belongs to an application, whose guardrail applies; required
    # otherwise (ADR-0039 §2).
    bundle: BundleRef | None = Field(
        None,
        description="The guardrail to evaluate against. Leave it out with an application's "
        "key: the application's guardrail applies.",
    )
    bundle_inline: PolicyBundle | None = Field(  # development only: not in the public docs
        None, json_schema_extra={"x-umai-internal": True}
    )
    phase: Phase = Field(description="What is being checked; see the phases table.")
    content: Content = Field(description="The content to check.")
    subject: Subject = Field(
        default_factory=Subject, description="Who acts; facts the guardrail's policies can use."
    )
    destination: Destination = Field(
        default_factory=Destination, description="Where the content goes."
    )
    action: AgentAction | None = Field(
        None, description="The agent's action, for the agent phases."
    )
    timeout_ms: int | None = Field(
        None,
        ge=50,
        le=60_000,
        description="Time budget for the whole evaluation in milliseconds; capped by the service.",
    )
    flags: Flags = Field(default_factory=Flags, description="Options for this call.")


# ------------------------------------------------------------------ response


class MatchedRule(ContractModel):
    """What produced (part of) a decision: a guardrail policy, the legacy per-type action, an
    L0 exception, an L1a rule or an L1b decision policy (0.12). Ids and versions only, never
    content."""

    kind: Literal["l0_policy", "l0_on_detect", "l0_exception", "l1a_rule", "l1b_policy"] = Field(
        description="What matched: a data-protection policy (`l0_policy`), an exception "
        "(`l0_exception`), a rule (`l1a_rule`) or a decision-model policy (`l1b_policy`)."
    )
    id: str = Field(description="The policy's, exception's or rule's id.")
    version: int | None = Field(None, description="The policy's version, when it has one.")
    name: str | None = Field(None, description="The policy's display name.")
    action: Action = Field(description="The action this match asked for.")
    entities: list[str] = Field(
        default_factory=list, description="Entity types involved, e.g. `TR_TCKN`."
    )


class Decision(ContractModel):
    action: Action = Field(description="What to do; see acting on a decision.")
    allowed: bool = Field(description="`true` when the content may go on (perhaps masked).")
    severity: Severity = Field(description="How serious the finding is.")
    reason: str = Field(
        description="Short explanation with entity types and ids, never the content."
    )
    decided_by: Literal["L0", "L1a", "L1b", "pipeline"] = Field(
        description="Which layer decided: `L0` data protection, `L1a` rules, `L1b` decision "
        "model, `pipeline` the guardrail's failure policy."
    )
    # Strictest first; empty when no rule applied (e.g. failure policy, nothing detected).
    matched: list[MatchedRule] = Field(
        default_factory=list, description="What produced the decision, strictest first."
    )


class Tag(ContractModel):
    code: str = Field(description="What was found, e.g. `data.pii.tr_tckn`.")
    layer: Layer = Field(description="The layer that found it.")
    score: float | None = Field(None, description="Detection confidence (0–1), for `L0`.")
    probability: float | None = Field(
        None, description="The decision model's probability (0–1), for `L1b`."
    )
    policy_id: str | None = Field(None, description="The policy that produced the tag.")
    policy_version: int | None = Field(None, description="That policy's version.")
    rule_id: str | None = Field(None, description="The rule that produced the tag.")


class OutputModifications(ContractModel):
    redacted_text: str = Field(
        description="The scanned text with the listed entity types replaced by placeholders "
        "such as `<TR_TCKN_1>`; send this instead of the original."
    )
    entities: list[str] = Field(description="The entity types that were replaced.")


class BundleInfo(ContractModel):
    id: str = Field(description="The guardrail's id.")
    version: int | None = Field(None, description="The guardrail version that decided.")
    mode: Mode | None = Field(
        None, description="`ENFORCE`, or `MONITOR` (decisions are recorded, never enforced)."
    )
    sha256: str | None = Field(None, description="Hash of that exact guardrail version.")


class LatencyMs(ContractModel):
    total: float = Field(description="Time spent in UMAI for this call, in milliseconds.")
    l0: float | None = Field(None, description="Data protection (entity detection).")
    l1a: float | None = Field(None, description="Rules.")
    l1b: float | None = Field(None, description="Decision-model policies.")


class TraceEntry(ContractModel):
    layer: Layer = Field(description="The layer.")
    outcome: str = Field(description="What the layer concluded.")
    latency_ms: float = Field(description="Time the layer took, in milliseconds.")
    provider: str | None = Field(None, description="Decision-model provider, for `L1b`.")
    model: str | None = Field(None, description="Decision model, for `L1b`.")
    prompt: str | None = Field(None, description="Reserved.")


class ErrorInfo(ContractModel):
    layer: Layer = Field(description="Where the failure happened.")
    type: str = Field(description="Failure type, e.g. `L1B_TIMEOUT`; see the error catalogue.")
    message: str | None = Field(None, description="Explanation; never contains content.")
    retryable: bool | None = Field(None, description="A retry may succeed.")


class EvaluationResponse(ContractModel):
    contract_version: Literal[
        "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "0.10", "0.11", "0.12"
    ] = Field(CONTRACT_VERSION, description="Version of the answer's shape.")
    request_id: str = Field(description="Your `request_id`.")
    tenant_id: str = Field(description="Your organization's id.")
    bundle: BundleInfo = Field(description="The exact guardrail version that decided.")
    phase: Phase = Field(description="The phase you sent.")
    decision: Decision = Field(description="The decision to act on.")
    would_have: Decision | None = Field(
        None,
        description="In `MONITOR` mode: what `ENFORCE` would have decided; `decision` is then "
        "`ALLOW`.",
    )
    degraded: bool = Field(
        False,
        description="A part of the evaluation failed and the guardrail's failure policy was "
        "applied; details in `errors`.",
    )
    tags: list[Tag] = Field(default_factory=list, description="What was found.")
    entities: list[EntitySummary] = Field(
        default_factory=list, description="Entity types found with counts; never values."
    )
    output_modifications: OutputModifications | None = Field(
        None, description="The masked text, when the action is `ALLOW_WITH_MODIFICATIONS`."
    )
    event_id: str = Field(
        description="Id of the recorded decision; quote it when you contact support."
    )
    review_queued: bool = Field(False, description="Reserved; always `false`.")
    latency_ms: LatencyMs = Field(description="Time spent, in milliseconds.")
    trace: list[TraceEntry] | None = Field(
        None, description="Per-layer trace, when you sent `flags.trace`."
    )
    errors: list[ErrorInfo] = Field(
        default_factory=list, description="Failures inside the evaluation; empty normally."
    )


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


class ErrorDetail(ContractModel):
    error: str = Field(description="Stable error code; see the error catalogue.")
    detail: str = Field(description="Explanation; never contains request content.")


class ErrorBody(ContractModel):
    """Admission and request refusals (evaluation API 0.6): the shape every 4xx shares;
    413, 422 ``invalid_request`` and 429 ``quota_exceeded`` add fields (models below)."""

    detail: ErrorDetail


class InputTooLargeDetail(ContractModel):
    error: Literal["input_too_large"] = "input_too_large"
    detail: str
    limit: int
    size: int
    unit: Literal["characters"] = "characters"


class QuotaExceededDetail(ContractModel):
    error: Literal["quota_exceeded"] = "quota_exceeded"
    detail: str
    limit: int
    used: int
    environment: Literal["live", "test"]
    resets_at: datetime


class QuotaExceededBody(ContractModel):
    """HTTP 429 body (evaluation API 0.13, ADR-0043): the month's requests are used; nothing was
    evaluated."""

    detail: QuotaExceededDetail


class InputTooLargeBody(ContractModel):
    """HTTP 413 body (evaluation API 0.9): nothing was evaluated, nothing was truncated."""

    detail: InputTooLargeDetail
