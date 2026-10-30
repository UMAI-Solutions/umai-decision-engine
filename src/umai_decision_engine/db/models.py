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
