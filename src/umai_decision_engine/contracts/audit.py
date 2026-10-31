"""Audit contract (docs/contracts/audit.md, ADR-0045): who changed what in a tenant's
configuration, when, and with which outcome. Ids, names of non-secret fields and their values
before and after; never content, secrets, key material, tokens, card or billing details."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from umai_decision_engine.contracts.common import ContractModel

CONTRACT_VERSION = "0.1"


class AuditAction(StrEnum):
    """One value per changing policy-service route. A route whose permission covers one change
    uses the permission's name (``key.create``); one that covers several gets a verb under it
    (``invitation.create``, ``invitation.revoke`` for ``invitation.manage``)."""

    DRAFT_CREATE = "guardrail.draft.create"
    DRAFT_SAVE = "guardrail.draft.save"
    DRAFT_DISCARD = "guardrail.draft.discard"
    DRAFT_TEST = "guardrail.draft.test"
    GUARDRAIL_PUBLISH = "guardrail.publish"
    GUARDRAIL_ACTIVATE = "guardrail.activate"
    TENANT_CREATE = "tenant.create"
    TENANT_PLAN = "tenant.plan"
    KEY_CREATE = "key.create"
    KEY_REVOKE = "key.revoke"
    KEY_UPDATE = "key.update"
    KEY_PUBLISH = "key.publish"
    ORGANIZATION_CREATE = "organization.create"
    ORGANIZATION_UPDATE = "organization.update"
    ORGANIZATION_EXPORT = "organization.export"
    MEMBER_UPDATE = "member.update"
    MEMBER_REMOVE = "member.remove"
    MEMBER_LEAVE = "member.leave"
    INVITATION_CREATE = "invitation.create"
    INVITATION_REVOKE = "invitation.revoke"
    INVITATION_RESEND = "invitation.resend"
    INVITATION_ACCEPT = "invitation.accept"
    APPLICATION_SAVE = "application.save"
    APPLICATION_DELETE = "application.delete"
    PROVIDER_SAVE = "provider.save"
    PROVIDER_DELETE = "provider.delete"
    GATEWAY_SETTINGS_UPDATE = "gateway.settings.update"
    BILLING_ACCOUNT_UPDATE = "billing.account.update"
    BILLING_CHECKOUT_START = "billing.checkout.start"
    BILLING_CHECKOUT_COMPLETE = "billing.checkout.complete"
    BILLING_PLAN_CHANGE = "billing.plan.change"
    BILLING_CANCEL = "billing.cancel"
    WEBHOOK_CREATE = "webhook.create"
    WEBHOOK_UPDATE = "webhook.update"
    WEBHOOK_DELETE = "webhook.delete"
    WEBHOOK_SECRET_ROTATE = "webhook.secret.rotate"
    WEBHOOK_TEST = "webhook.test"
    AUDIT_EXPORT = "audit.export"


TargetType = Literal[
    "guardrail",
    "tenant",
    "organization",
    "api_key",
    "member",
    "invitation",
    "application",
    "provider",
    "gateway_settings",
    "billing_account",
    "subscription",
    "checkout",
    "webhook_endpoint",
    "audit_log",
]

# success: the change happened · denied: refused for the caller's rights (HTTP 403)
Outcome = Literal["success", "denied"]


class AuditActor(ContractModel):
    type: Literal["person", "operator"]
    sub: str | None = None  # the identity service's subject (from the on-behalf-of assertion)
    user_id: uuid.UUID | None = None  # the person in the tenant store, as member lists show it
    # Resolved when read, never stored with the event; absent once the person is erased.
    email: str | None = None
    name: str | None = None


class AuditTarget(ContractModel):
    type: TargetType
    id: str | None = None  # None for a tenant's single object (gateway settings, billing account)


class AuditEvent(ContractModel):
    contract_version: Literal["0.1"] = CONTRACT_VERSION
    event_id: str = Field(pattern=r"^au-[0-9a-f]{32}$")
    occurred_at: datetime
    tenant_id: str
    actor: AuditActor
    action: AuditAction
    target: AuditTarget
    outcome: Outcome
    request_id: str = Field(max_length=128)
    details: dict[str, Any] = Field(default_factory=dict)


class AuditEventPage(ContractModel):
    """``GET /v1/audit-events``: newest first; ``next_cursor`` continues the listing."""

    events: list[AuditEvent]
    next_cursor: str | None = None


# The CSV export's columns, in order (``GET /v1/audit-events/export``).
CSV_COLUMNS: tuple[str, ...] = (
    "occurred_at",
    "event_id",
    "actor_type",
    "actor_user_id",
    "actor_sub",
    "actor_email",
    "action",
    "target_type",
    "target_id",
    "outcome",
    "request_id",
    "details",
)
