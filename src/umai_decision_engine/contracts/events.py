"""Records (ADR-0041): the decision event and the L1b outcome record (docs/contracts/
tagged-action.md, version in ``CONTRACT_VERSION``) and the usage record (docs/contracts/usage.md,
``USAGE_CONTRACT_VERSION``), and their read API in the policy service. Ids, types, counts,
codes, actions, probabilities and latencies only: no content, not even redacted (ADR-0041 §2)."""

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

CONTRACT_VERSION = "0.8"  # tagged-action
USAGE_CONTRACT_VERSION = "0.4"  # usage

# ``browser``: the evaluation API called with a browser connection (tagged-action 0.8, usage 0.4,
# ADR-0058).
Source = Literal["api", "gateway", "browser"]
Environment = Literal["live", "test"]


class RecordContext(ContractModel):
    """Who called, for the records of one evaluation: set by the engine API or the gateway
    from the admitted credential, never by the caller's body."""

    source: Source = "api"
    key_id: str | None = None
    environment: Environment | None = None
    application_id: str | None = None
    gateway_call_id: str | None = None
    # A browser connection's person (``users.id``, tagged-action 0.8): from the key, never the body.
    user_id: str | None = None
    # False: the records are built but held on the evaluation for the caller to commit or drop
    # (the window re-checks of a gateway stream, ADR-0041 §4). Never serialized.
    record: bool = True


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
    contract_version: Literal["0.3", "0.4", "0.5", "0.6", "0.7", "0.8"] = CONTRACT_VERSION
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
    user_id: str | None = None  # a browser connection's person (0.8, ADR-0058 D4)
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

    contract_version: Literal["0.3", "0.4", "0.5", "0.6", "0.7", "0.8"] = CONTRACT_VERSION
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


Meter = Literal[
    "evaluations",
    "l1b_calls",
    "l1b_input_tokens",
    "l1b_output_tokens",
    "gateway_calls",
    "provider_input_tokens",
    "provider_output_tokens",
]


class UsageRecord(ContractModel):
    contract_version: Literal["0.1", "0.2", "0.3", "0.4"] = USAGE_CONTRACT_VERSION
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
    provider: str | None = None  # gateway meters: the provider the call went to (0.2)
    model: str | None = None  # gateway meters: the upstream model (0.2)


Record = DecisionEvent | L1bOutcomeRecord | UsageRecord


# ------------------------------------------------------------------ read API (policy service)


class Person(ContractModel):
    """A person named by events (tagged-action 0.8), as the tenant store knows them at read time;
    both ``None`` for a person no longer in the store."""

    email: str | None = None
    name: str | None = None


class DecisionEventPage(ContractModel):
    """``GET /v1/decision-events``: newest first; ``next_cursor`` continues the listing.
    ``people``: the persons of the page's browser events (0.8)."""

    events: list[DecisionEvent]
    people: dict[str, Person] = Field(default_factory=dict)
    next_cursor: str | None = None


class DecisionEventDetail(ContractModel):
    """``GET /v1/decision-events/{event_id}``: the event and its L1b outcome records."""

    event: DecisionEvent
    l1b_outcomes: list[L1bOutcomeRecord] = Field(default_factory=list)
    people: dict[str, Person] = Field(default_factory=dict)  # the event's person (0.8)


class UsageGroup(ContractModel):
    meter: Meter
    source: Source
    environment: Environment | None = None
    application_id: str | None = None
    quantity: int = Field(ge=0)


_DATE = r"^\d{4}-\d{2}-\d{2}$"


class UsageDay(ContractModel):
    date: str = Field(pattern=_DATE)  # UTC calendar day
    meters: dict[str, int] = Field(default_factory=dict)  # meter → quantity, every source


class UsageSummary(ContractModel):
    """``GET /v1/usage``: one calendar month (UTC) of the ledger."""

    contract_version: Literal["0.2", "0.3"] = USAGE_CONTRACT_VERSION
    tenant_id: str
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    from_: datetime = Field(alias="from")
    to: datetime
    totals: dict[str, int] = Field(default_factory=dict)  # meter → quantity, every source
    groups: list[UsageGroup] = Field(default_factory=list)
    daily: list[UsageDay] = Field(default_factory=list)  # days with usage, oldest first (0.3)


class DecisionDay(ContractModel):
    date: str = Field(pattern=_DATE)  # UTC calendar day
    actions: dict[str, int] = Field(default_factory=dict)  # decision → events


class EntityCount(ContractModel):
    type: str
    decisions: int = Field(ge=0)  # events in which the type was found


class PolicyCount(ContractModel):
    kind: str
    id: str
    decisions: int = Field(ge=0)  # events the policy took part in deciding


class ApplicationCount(ContractModel):
    """One application's events in the window (0.7)."""

    application_id: str | None = None  # None: no application (operator token, unbound keys)
    actions: dict[str, int] = Field(default_factory=dict)  # decision → events
    last_event_at: datetime  # its newest event in the window


class LatencyStat(ContractModel):
    """Percentiles of one latency over the window's events that recorded it (0.7): the nearest
    rank, a value an event really took, the same on PostgreSQL and SQLite."""

    events: int = Field(0, ge=0)
    p50: float | None = None
    p95: float | None = None


class SummaryLatency(ContractModel):
    total: LatencyStat = Field(default_factory=LatencyStat)
    l0: LatencyStat = Field(default_factory=LatencyStat)
    l1b: LatencyStat = Field(default_factory=LatencyStat)  # events whose enforcing L1b call ran


class DecisionEventSummary(ContractModel):
    """``GET /v1/decision-events/summary`` (0.5): counts for charts over the same filters as the
    list. ``top_entities`` and ``top_policies`` come from the newest ``scanned`` events;
    ``truncated`` says the window held more than the scan limit. Per application and latency
    percentiles (0.7) are exact."""

    since: datetime
    until: datetime
    total: int = Field(ge=0)
    by_action: dict[str, int] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)
    by_day: list[DecisionDay] = Field(default_factory=list)  # days with events, oldest first
    top_entities: list[EntityCount] = Field(default_factory=list)
    top_policies: list[PolicyCount] = Field(default_factory=list)
    scanned: int = Field(0, ge=0)
    truncated: bool = False
    by_application: list[ApplicationCount] = Field(default_factory=list)  # most events first
    latency_ms: SummaryLatency = Field(default_factory=SummaryLatency)
