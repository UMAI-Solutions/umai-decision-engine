"""ORM models. Surrogate UUID keys everywhere (ADR-0025 §1): referential-integrity checks
bypass RLS, so natural keys could leak existence through constraint errors.

Columns hold ids, hashes, names, timestamps and policy data (draft bodies are policy, not
content: ADR-0012). No column ever carries user content (ADR-0005)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """``DateTime(timezone=True)`` that always reads back UTC-aware values. Postgres keeps the
    zone; SQLite (development) drops it, and a naive value serialized to the console reads as
    the browser's local time."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)  # written in UTC
        return value


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    """Global table (allowlisted). ``slug`` is the external ``tenant_id`` of the evaluation
    API and the key registry; ``id`` is what RLS compares."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    region: Mapped[str] = mapped_column(String(8), nullable=False)  # eu | us | sh
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)
    # ADR-0033 §1, §7: organizations are tenants
    edition: Mapped[str] = mapped_column(String(16), nullable=False, default="community")
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)  # users.id
    # The terms the creator accepted (organizations 0.3)
    terms_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    terms_accepted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)  # users.id
    terms_accepted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    # An operator's limits over the edition's plan (entitlements 0.1, ADR-0043); null: none
    entitlement_overrides: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class User(Base):
    """Global table (allowlisted, ADR-0033 §2): one person of this regional stack. Account
    metadata only: the identity service's subject, e-mail, display name."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)


class Membership(Base):
    """Tenant table with the extra ``member_self`` read policy (ADR-0033 §2)."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    invited_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)


class Invitation(Base):
    """Tenant table. The link's secret is stored as SHA-256 only (like API keys)."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class ApiKey(Base):
    """Tenant table. Stores sha256(secret) only (ADR-0023 §2)."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_id: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    secret_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    env: Mapped[str] = mapped_column(String(8), nullable=False)  # live | test
    region: Mapped[str] = mapped_column(String(8), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    # The application the key belongs to (credentials 0.6, ADR-0039); fixed at creation.
    application_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Networks the key may be used from, canonical CIDRs (credentials 0.9, ADR-0046); NULL: any.
    allowed_ips: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class Draft(Base):
    """Tenant table: one open draft per guardrail (control plane, ADR-0022)."""

    __tablename__ = "drafts"
    __table_args__ = (UniqueConstraint("tenant_id", "bundle_id", name="uq_drafts_tenant_bundle"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bundle_id: Mapped[str] = mapped_column(String(128), nullable=False)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    bundle: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)


def _tenant_fk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)


class DecisionEventRow(Base):
    """Tenant table, append-only (ADR-0041): one decision event per evaluation, contract
    tagged-action 0.3. Ids, types, counts, codes and actions only; never content."""

    __tablename__ = "decision_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", name="uq_decision_events_tenant_event"),
        Index("ix_decision_events_tenant_occurred", "tenant_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    key_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(8), nullable=True)
    application_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gateway_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bundle_id: Mapped[str] = mapped_column(String(128), nullable=False)
    bundle_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bundle_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bundle_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(16), nullable=False)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    floor_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    would_have: Mapped[str | None] = mapped_column(String(32), nullable=True)
    review: Mapped[bool] = mapped_column(Boolean, nullable=False)
    review_reasons: Mapped[list] = mapped_column(JSON, nullable=False)
    subject: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    destination: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agent_action: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    entities: Mapped[list] = mapped_column(JSON, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, nullable=False)
    matched: Mapped[list] = mapped_column(JSON, nullable=False)
    l1b: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    errors: Mapped[list] = mapped_column(JSON, nullable=False)
    latency_total_ms: Mapped[float] = mapped_column(Float, nullable=False)
    latency_l0_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_l1b_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class L1bOutcomeRow(Base):
    """Tenant table, append-only (ADR-0041): one L1b policy's outcome for one evaluation,
    enforcing or shadow."""

    __tablename__ = "l1b_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "event_id", "policy_id", name="uq_l1b_outcomes_tenant_event_policy"
        ),
        Index("ix_l1b_outcomes_tenant_occurred", "tenant_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    bundle_id: Mapped[str] = mapped_column(String(128), nullable=False)
    bundle_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    enforcement: Mapped[str] = mapped_column(String(8), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(8), nullable=False)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    rule: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, nullable=False)
    review_reasons: Mapped[list] = mapped_column(JSON, nullable=False)
    error: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class UsageEventRow(Base):
    """Tenant table, append-only (ADR-0041, ADR-0028 §1): the usage ledger, contract usage
    0.1. ``(tenant_id, event_id, meter)`` is unique: a retried write never counts twice."""

    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "event_id", "meter", name="uq_usage_events_tenant_event_meter"
        ),
        Index("ix_usage_events_tenant_meter_occurred", "tenant_id", "meter", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    meter: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    key_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(8), nullable=True)
    application_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bundle_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bundle_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)  # gateway meters (0007)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)


# ------------------------------------------------------------------ billing (ADR-0044)


class BillingAccountRow(Base):
    """Tenant table (ADR-0044 D4): the organization's billing details, one row per tenant. No
    national identity number and no card data are ever stored."""

    __tablename__ = "billing_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_billing_accounts_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_number: Mapped[str] = mapped_column(String(11), nullable=False)
    tax_office: Mapped[str] = mapped_column(String(100), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(100), nullable=False)
    contact_surname: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    phone: Mapped[str] = mapped_column(String(16), nullable=False)
    address: Mapped[str] = mapped_column(String(300), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    zip_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)


class SubscriptionRow(Base):
    """Tenant table (ADR-0044 D4): the tenant's subscription and its state machine (contract
    billing 0.1). ``parent_reference`` is the provider's stable key; ``reference`` changes with
    every plan change."""

    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_subscriptions_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    reference: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pricing_plan_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    edition: Mapped[str] = mapped_column(String(16), nullable=False)
    pending_edition: Mapped[str | None] = mapped_column(String(16), nullable=True)  # downgrade
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    cancel_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    grace_until: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_order_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_payment_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)


class BillingCheckoutRow(Base):
    """Tenant table (ADR-0044 D2): a hosted form we opened, found again by its id when the
    buyer returns; the provider's token proves nothing until the form result is read."""

    __tablename__ = "billing_checkouts"
    __table_args__ = (UniqueConstraint("tenant_id", "checkout_id", name="uq_billing_checkouts"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    checkout_id: Mapped[str] = mapped_column(String(64), nullable=False)
    token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    edition: Mapped[str] = mapped_column(String(16), nullable=False)
    pricing_plan_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # open | active | pending | failed
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class BillingEventRow(Base):
    """Tenant table, append-only (ADR-0044 D4, D9): what happened to the subscription, payments
    with amount and currency for invoicing later. ``provider_event_id`` (iyzico's
    ``iyziReferenceCode``) is unique per tenant: a repeated webhook is applied once."""

    __tablename__ = "billing_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider_event_id", name="uq_billing_events_provider_event"),
        Index("ix_billing_events_tenant_occurred", "tenant_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subscription_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[str | None] = mapped_column(String(16), nullable=True)  # decimal as text
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class BillingLinkRow(Base):
    """Global table (allowlisted, ADR-0044 D4): which tenant a provider subscription reference
    belongs to, so a webhook (which names no tenant) is routed without crossing RLS. References
    only, no personal data."""

    __tablename__ = "billing_links"

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    reference: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)


class ConsoleSessionRow(Base):
    """Global table (allowlisted, ADR-0024 §2 amendment): one console sign-in of a person. The
    session id lives in the console's encrypted cookie; this row keeps only its SHA-256, a coarse
    device label and times. No IP address, no user agent, no content."""

    __tablename__ = "console_sessions"
    __table_args__ = (Index("ix_console_sessions_expires_at", "expires_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sid_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    # signed_out | revoked | signed_out_elsewhere | limit (contract console-sessions 0.1)
    revoke_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


# ------------------------------------------------------------------ audit (ADR-0045)


class AuditEventRow(Base):
    """Tenant table, append-only (ADR-0045, ADR-0030 §3): one configuration change or refused
    attempt, contract audit 0.1. The actor is a subject and a user id, never an e-mail; details
    are ids and non-secret before/after values, never content or secrets."""

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", name="uq_audit_events_tenant_event"),
        Index("ix_audit_events_tenant_occurred", "tenant_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False)


# ------------------------------------------------------------------ webhooks (ADR-0048)


class WebhookEndpointRow(Base):
    """Tenant table (ADR-0048 §4): an organization's endpoint. The URL is stored AES-GCM-encrypted
    (it can carry a credential; Slack's always does) and the signing secret is not stored at all:
    it is derived from the deployment's master key and ``secret_version``."""

    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    url_sealed: Mapped[str] = mapped_column(String(4096), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # generic | slack
    event_types: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    enabled_since: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    disabled_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    secret_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    secret_rotated_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failing_since: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)


class WebhookMessageRow(Base):
    """Tenant table (ADR-0048 §3–§4): the outbox, one event for one endpoint, with its delivery
    state. ``(tenant_id, endpoint_id, dedupe_key)`` is unique: an event read twice (after a restart)
    is sent once. ``data`` is the event's contract ``data`` (ids, types, codes, counts)."""

    __tablename__ = "webhook_messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "endpoint_id", "dedupe_key", name="uq_webhook_messages_key"),
        Index("ix_webhook_messages_tenant_due", "tenant_id", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    # No foreign key (like every reference between tenant tables here): referential checks
    # bypass RLS; deleting an endpoint deletes its messages explicitly.
    endpoint_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_id: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # pending|delivered|failed|canceled
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=_now)


class WebhookDeliveryRow(Base):
    """Tenant table, append-only (ADR-0048 §4): one row per attempt. Status code, error code and
    duration only: the receiver's answer body is never read or stored."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_tenant_attempted", "tenant_id", "attempted_at"),
        Index("ix_webhook_deliveries_endpoint", "tenant_id", "endpoint_id", "attempted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    endpoint_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)  # no FK: outlives it
    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_id: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # delivered | failed
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
