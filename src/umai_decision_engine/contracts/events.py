"""Records (ADR-0041): the decision event and the L1b outcome record (docs/contracts/
tagged-action.md, version in ``CONTRACT_VERSION``) and the usage record (docs/contracts/usage.md,
``USAGE_CONTRACT_VERSION``). Ids, types, counts, codes, actions, probabilities and latencies
only: no content, not even redacted (ADR-0041 §2)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from umai_decision_engine.contracts.common import (
    Action,
    ContractModel,
    Layer,
    Mode,
    Phase,
    Severity,
)
from umai_decision_engine.contracts.entities import EntitySummary
from umai_decision_engine.contracts.evaluation import Destination, Tag

CONTRACT_VERSION = "0.3"  # tagged-action
USAGE_CONTRACT_VERSION = "0.1"  # usage

Source = Literal["api", "gateway"]
Environment = Literal["live", "test"]


class RecordContext(ContractModel):
    """Who called, for the records of one evaluation: set by the engine API or the gateway
    from the admitted credential, never by the caller's body."""

    source: Source = "api"
    key_id: str | None = None
    environment: Environment | None = None
    application_id: str | None = None
    gateway_call_id: str | None = None


class EventBundle(ContractModel):
    id: str
    version: int | None = None
    sha256: str | None = None
    mode: Mode | None = None


class EventSubject(ContractModel):
    """Role and department only: user and session ids are not recorded (ADR-0041 §2)."""

    role: str | None = None
    department: str | None = None


class EventAgentAction(ContractModel):
    """The tool an agent action targets; never its arguments or metadata."""

    tool_name: str | None = None
    server_name: str | None = None


class EventMatched(ContractModel):
    kind: Literal["l0_policy", "l0_on_detect", "l0_exception", "l1a_rule", "l1b_policy"]
    id: str
    version: int | None = None
    action: Action


class EventDecision(ContractModel):
    action: Action
    severity: Severity
    decided_by: Literal["L0", "L1a", "L1b", "pipeline"]
    degraded: bool = False
    floor: Action | None = None  # the L0 gate's outcome when L1 ran on top of it (ADR-0036)
    would_have: Action | None = None  # MONITOR mode: what ENFORCE would have done


class EventError(ContractModel):
    """Error type only: messages can carry exception text and are not recorded."""

    layer: Layer
    type: str
    retryable: bool | None = None


class EventL1b(ContractModel):
    """L1b summary; per-policy outcomes are separate records (:class:`L1bOutcomeRecord`)."""

    provider: str | None = None
    enforcing: bool = False  # at least one enforcing policy applied
    shadow: bool = False  # at least one shadow policy applied
    failed: str | None = None  # error kind of the enforcing call
    latency_ms: float | None = None


class EventLatency(ContractModel):
    total: float
    l0: float | None = None
    l1b: float | None = None


class DecisionEvent(ContractModel):
    contract_version: Literal["0.3"] = CONTRACT_VERSION
    event_id: str = Field(min_length=1, max_length=64)
    request_id: str = Field(max_length=128)
    tenant_id: str
    occurred_at: datetime
    source: Source
    engine_version: str
    key_id: str | None = None
    environment: Environment | None = None
    application_id: str | None = None
    gateway_call_id: str | None = None
    bundle: EventBundle
    phase: Phase
    subject: EventSubject | None = None
    destination: Destination | None = None
    agent_action: EventAgentAction | None = None
    entities: list[EntitySummary] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    matched: list[EventMatched] = Field(default_factory=list)
    decision: EventDecision
    l1b: EventL1b | None = None
    review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    errors: list[EventError] = Field(default_factory=list)
    latency_ms: EventLatency


class L1bOutcomeRecord(ContractModel):
    """One L1b policy's outcome for one evaluation. A shadow outcome is written when its
    background call finishes, after the decision event (ADR-0041 §1)."""

    contract_version: Literal["0.3"] = CONTRACT_VERSION
    event_id: str = Field(min_length=1, max_length=64)
    tenant_id: str
    occurred_at: datetime
    bundle_id: str
    bundle_version: int | None = None
    phase: Phase
    policy_id: str
    policy_version: int
    enforcement: Literal["enforce", "shadow"]
    provider: str | None = None
    outcome: Literal["decided", "failed"]
    action: Action | None = None  # None when the call failed
    severity: Severity | None = None
    probability: float | None = Field(None, ge=0, le=1)
    rule: int | None = None  # index of the matching decide rule; None: the default
    tags: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    error: str | None = None  # error kind when ``outcome`` is ``failed``
    latency_ms: float | None = None


Meter = Literal["evaluations", "l1b_calls", "l1b_input_tokens", "l1b_output_tokens"]


class UsageRecord(ContractModel):
    contract_version: Literal["0.1"] = USAGE_CONTRACT_VERSION
    event_id: str = Field(min_length=1, max_length=64)
    tenant_id: str
    occurred_at: datetime
    meter: Meter
    quantity: int = Field(ge=0)
    source: Source
    key_id: str | None = None
    environment: Environment | None = None
    application_id: str | None = None
    bundle_id: str | None = None
    bundle_version: int | None = None
    decision: Action | None = None
    degraded: bool = False


Record = DecisionEvent | L1bOutcomeRecord | UsageRecord
