"""Webhooks contract (docs/contracts/webhooks.md, ADR-0048): an organization's outbound endpoints,
the events they receive and the delivery log. Payloads carry ids, types, codes, counts and console
links only, never content (ADR-0005): every model here forbids unknown fields."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args

from pydantic import Field, model_validator

from umai_decision_engine.contracts.common import ContractModel
from umai_decision_engine.contracts.entitlements import QuotaEnvironment
from umai_decision_engine.contracts.organizations import Edition

CONTRACT_VERSION = "0.1"

EndpointKind = Literal["generic", "slack"]
# What an endpoint may subscribe to; ``webhook.test`` is only sent by the test route.
EventType = Literal[
    "decision.blocked",
    "quota.threshold",
    "billing.payment_failed",
    "billing.payment_succeeded",
    "billing.subscription_changed",
]
EVENT_TYPES: tuple[str, ...] = get_args(EventType)
AnyEventType = Literal[EventType, "webhook.test"]
# manual: someone turned it off · failing: every attempt failed for the configured time ·
# gone: the receiver said the URL no longer exists (HTTP 410; Slack 403/404/410)
DisabledReason = Literal["manual", "failing", "gone"]
DeliveryOutcome = Literal["delivered", "failed"]
# http_status: a non-2xx answer · redirect: a 3xx answer (never followed) · timeout ·
# connection: DNS, TCP or TLS failed · blocked_address: the host resolves to an address webhooks
# may not reach (private, loopback, link-local, metadata)
DeliveryError = Literal["http_status", "redirect", "timeout", "connection", "blocked_address"]


# ------------------------------------------------------------------ endpoints


class EndpointCreate(ContractModel):
    url: str = Field(min_length=9, max_length=2048)
    kind: EndpointKind = "generic"
    event_types: list[EventType] = Field(min_length=1, max_length=len(EVENT_TYPES))
    description: str | None = Field(None, max_length=200)
    enabled: bool = True


class EndpointUpdate(ContractModel):
    """``PATCH``: a field that is present replaces the stored value."""

    url: str | None = Field(None, min_length=9, max_length=2048)
    event_types: list[EventType] | None = Field(None, min_length=1, max_length=len(EVENT_TYPES))
    description: str | None = Field(None, max_length=200)
    enabled: bool | None = None


class EndpointView(ContractModel):
    endpoint_id: str
    kind: EndpointKind
    url: str  # masked: scheme, host and the URL's last four characters
    event_types: list[EventType]
    description: str | None = None
    enabled: bool
    disabled_reason: DisabledReason | None = None
    disabled_at: datetime | None = None
    signed: bool  # generic: X-Umai-Signature; slack: the URL is the secret
    secret_rotated_at: datetime | None = None
    consecutive_failures: int = Field(ge=0)
    failing_since: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    created_at: datetime
    created_by: str
    updated_at: datetime


class EndpointCreated(ContractModel):
    """``POST /v1/webhooks/endpoints`` and the secret rotation: the signing secret, once."""

    endpoint: EndpointView
    secret: str | None = None  # generic only; null for slack


class WebhooksView(ContractModel):
    """``GET /v1/webhooks``."""

    contract_version: Literal["0.1"] = CONTRACT_VERSION
    tenant_id: str
    available: bool  # false: this deployment sends no webhooks
    event_types: list[EventType]
    max_endpoints: int = Field(ge=0)
    endpoints: list[EndpointView] = Field(default_factory=list)


# ------------------------------------------------------------------ deliveries


class DeliveryView(ContractModel):
    delivery_id: str
    endpoint_id: str
    event_id: str
    event_type: AnyEventType
    attempt: int = Field(ge=1)
    attempted_at: datetime
    outcome: DeliveryOutcome
    status_code: int | None = None
    error: DeliveryError | None = None
    duration_ms: float | None = Field(None, ge=0)
    next_attempt_at: datetime | None = None  # a retry is scheduled
    test: bool = False


class DeliveryPage(ContractModel):
    """``GET /v1/webhooks/deliveries``, newest first."""

    deliveries: list[DeliveryView]
    next_cursor: str | None = None


class EndpointTestResult(ContractModel):
    """``POST /v1/webhooks/endpoints/{id}/test``: the one attempt, made while the caller waits."""

    delivery: DeliveryView


# ------------------------------------------------------------------ events (what receivers get)


class Window(ContractModel):
    from_: datetime = Field(alias="from")
    to: datetime


class ApplicationCount(ContractModel):
    application_id: str | None = None  # null: evaluation API calls without an application
    blocked: int = Field(ge=0)


class EntityCount(ContractModel):
    type: str
    decisions: int = Field(ge=0)


class PolicyCount(ContractModel):
    kind: str
    id: str
    decisions: int = Field(ge=0)


class DecisionBlockedData(ContractModel):
    """Blocked decisions of one aggregation window: one event per window, never one per call."""

    window: Window
    blocked: int = Field(ge=1)
    by_source: dict[Literal["api", "gateway"], int]
    by_phase: dict[str, int]
    applications: list[ApplicationCount] = Field(max_length=5)
    top_entities: list[EntityCount] = Field(max_length=5)
    top_policies: list[PolicyCount] = Field(max_length=5)
    sample_event_ids: list[str] = Field(max_length=5)  # newest first; open them in the console
    scanned: int = Field(ge=0)  # events whose entities and policies were counted
    truncated: bool  # more blocked events than were scanned


class QuotaThresholdData(ContractModel):
    environment: QuotaEnvironment
    threshold_percent: Literal[80, 100]
    limit: int = Field(ge=1)
    used: int = Field(ge=0)
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    resets_at: datetime
    edition: Edition


class BillingPaymentData(ContractModel):
    amount: str | None = None  # decimal as text, VAT included
    currency: str | None = None
    edition: Edition | None = None
    subscription_status: str | None = None
    grace_until: datetime | None = None  # payment_failed: Community limits after this


class BillingSubscriptionData(ContractModel):
    change: Literal["started", "plan_changed", "canceled", "grace_ended", "ended"]
    edition: Edition | None = None
    effective: Literal["now", "next_period"] | None = None  # plan_changed
    subscription_status: str | None = None


class WebhookTestData(ContractModel):
    endpoint_id: str


EventData = (
    DecisionBlockedData
    | QuotaThresholdData
    | BillingPaymentData
    | BillingSubscriptionData
    | WebhookTestData
)
DATA_MODELS: dict[str, type[ContractModel]] = {
    "decision.blocked": DecisionBlockedData,
    "quota.threshold": QuotaThresholdData,
    "billing.payment_failed": BillingPaymentData,
    "billing.payment_succeeded": BillingPaymentData,
    "billing.subscription_changed": BillingSubscriptionData,
    "webhook.test": WebhookTestData,
}


class Links(ContractModel):
    console: str | None = None  # absolute URL of the console page; absent when not configured


class WebhookEvent(ContractModel):
    """The body of a ``generic`` delivery; retries resend the same ``event_id`` and body."""

    contract_version: Literal["0.1"] = CONTRACT_VERSION
    event_id: str = Field(pattern=r"^evt_[0-9a-f]{32}$")
    type: AnyEventType
    occurred_at: datetime
    organization_id: str
    data: dict
    links: Links = Field(default_factory=Links)

    @model_validator(mode="after")
    def _data_matches_type(self) -> WebhookEvent:
        DATA_MODELS[self.type].model_validate(self.data)
        return self
