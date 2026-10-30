"""Policy service HTTP API (ADR-0022).

uvicorn umai_decision_engine.control_plane.app:app --port 9200
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import json
import logging
import re
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from umai_decision_engine import __version__
from umai_decision_engine.applications import (
    AllowList,
    read_application_file,
    validate_application_file,
    write_application_file,
)
from umai_decision_engine.applications import problem as application_problem
from umai_decision_engine.billing.iyzico import IyzicoClient
from umai_decision_engine.billing.service import BillingError, BillingService
from umai_decision_engine.contracts.audit import AuditAction
from umai_decision_engine.contracts.billing import (
    BillingAccount,
    CheckoutCompletion,
    CheckoutRequest,
    PlanChange,
)
from umai_decision_engine.contracts.common import Action as DecisionAction
from umai_decision_engine.contracts.common import Phase
from umai_decision_engine.contracts.credentials import KeyEnv, Scope
from umai_decision_engine.contracts.entitlements import EntitlementsView, PlanUpdate, RequestsUsed
from umai_decision_engine.contracts.gateway import (
    APPLICATION_ID,
    BUILTIN_OPENAI,
    PROVIDER_ID,
    ApplicationFile,
    Provider,
)
from umai_decision_engine.contracts.organizations import (
    InvitationAccept,
    InvitationCreate,
    MembershipUpdate,
    OrganizationCreate,
    OrganizationUpdate,
)
from umai_decision_engine.control_plane.actors import load_public_key
from umai_decision_engine.control_plane.audit import (
    EXPORT_MAX,
    Audit,
    AuditLog,
    AuditRoute,
    audited,
    before_after,
)
from umai_decision_engine.control_plane.audit import BadCursor as BadAuditCursor
from umai_decision_engine.control_plane.authz import (
    TENANT_HEADER,
    Action,
    Actor,
    actor_of,
    authorize,
    check_tenant,
)
from umai_decision_engine.control_plane.authz import allowed as _allowed
from umai_decision_engine.control_plane.keys import KeyPublisher, KeyService
from umai_decision_engine.control_plane.organizations import (
    DISPOSABLE_EMAIL_DOMAINS,
    OrganizationError,
    OrganizationService,
)
from umai_decision_engine.control_plane.plans import Plans
from umai_decision_engine.control_plane.records import BadCursor, RecordsReader, retention_loop
from umai_decision_engine.control_plane.repository import GuardrailRepositories
from umai_decision_engine.control_plane.service import PolicyService, ServiceError
from umai_decision_engine.db.migrate import upgrade
from umai_decision_engine.db.organizations import (
    InvitationRepository,
    MembershipRepository,
    UserRepository,
)
from umai_decision_engine.db.repositories import (
    ApiKeyRepository,
    Conflict,
    DraftRepository,
    NotFound,
    TenantInfo,
    TenantRepository,
)
from umai_decision_engine.db.session import make_engine, sqlite_url
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.l0.recognizers.catalog import SPECS_BY_ENTITY, TAG_BY_ENTITY
from umai_decision_engine.settings import Settings, get_settings
from umai_decision_engine.telemetry.logging import configure_logging
from umai_decision_engine.tenancy.quota import month_bounds

log = logging.getLogger("umai.control_plane")

_TESTS_KEY = b"umai-de-policy-service-tests"  # golden-test runs only; hashes are never shown

_EUROPE = {
    "UK_NINO",
    "DE_TAX_ID",
    "FR_NIR",
    "ES_NIF",
    "ES_NIE",
    "IT_FISCAL_CODE",
    "NL_BSN",
    "PL_PESEL",
}


def _catalog_group(entity: str) -> str:
    if TAG_BY_ENTITY.get(entity, "").startswith("data.secret."):
        return "Secrets"
    if entity.startswith("TR_"):
        return "Türkiye"
    if entity in _EUROPE:
        return "Europe"
    return "Global"


class DraftCreate(BaseModel):
    base_version: int | None = Field(None, ge=1)


class DraftSave(BaseModel):
    bundle: dict[str, Any]


class PublishRequest(BaseModel):
    activate: bool = False
    accept: list[str] = Field(default_factory=list)  # case ids whose new behaviour is accepted
    note: str | None = Field(None, max_length=500)


class ActivateRequest(BaseModel):
    version: int = Field(ge=1)
    note: str | None = Field(None, max_length=500)


class TenantCreate(BaseModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str | None = Field(None, max_length=200)
    region: Literal["eu", "us", "sh"] | None = None  # default: the service's region


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[Scope] | None = None  # default: evaluate only (ADR-0023 §3)
    env: KeyEnv = "live"
    expires_at: datetime | None = None
    # The application the key belongs to; its guardrail binds every call of the key (credentials
    # 0.6, ADR-0039). Scopes then default to evaluate and gateway.
    application_id: str | None = Field(None, pattern=APPLICATION_ID)


async def require_token(request: Request) -> None:
    token = request.app.state.settings.control_api_token
    if token is None:
        return  # development only; the app refuses to start without one elsewhere
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        value.encode(), token.get_secret_value().encode()
    ):
        raise HTTPException(401, "missing or invalid token", headers={"WWW-Authenticate": "Bearer"})


async def current_tenant(request: Request) -> TenantInfo:
    """The tenant a control-plane request acts for. A person: the tenant their assertion names
    (``X-Umai-Tenant``, if sent, must agree). The operator (control token alone): the
    ``X-Umai-Tenant`` header, else the configured default tenant (ADR-0023 §1, ADR-0033 §1)."""

    actor = actor_of(request)
    header = request.headers.get(TENANT_HEADER)
    if actor.kind == "person":
        if actor.tenant_id is None:
            raise HTTPException(
                403, {"error": "tenant_required", "detail": "the assertion names no tenant"}
            )
        check_tenant(actor, header or actor.tenant_id)
        slug = actor.tenant_id
    else:
        slug = header or request.app.state.settings.default_tenant_id
    tenant = await request.app.state.tenants.get(slug)
    if tenant is None:
        raise HTTPException(404, f"tenant {slug} does not exist")
    return tenant


router = APIRouter(prefix="/v1", dependencies=[Depends(require_token)], route_class=AuditRoute)
_TENANT = Depends(current_tenant)
# One dependency per action of the permission matrix (ADR-0029 §3); every route takes one.
_ALLOW = {action: Depends(authorize(action)) for action in Action}
# One per audit action (ADR-0045); every changing route takes one and names its target.
_AUDIT = {action: Depends(audited(action)) for action in AuditAction}


def _service(request: Request) -> PolicyService:
    return request.app.state.service


def _keys(request: Request) -> KeyService:
    return request.app.state.keys


@router.get("/catalog")
async def catalog(actor: Actor = _ALLOW[Action.CATALOG_READ]) -> dict[str, Any]:
    return {
        "entities": [
            {"type": entity, "tag": TAG_BY_ENTITY.get(entity), "group": _catalog_group(entity)}
            for entity in sorted(SPECS_BY_ENTITY)
        ]
    }


@router.get("/guardrails")
async def list_guardrails(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GUARDRAIL_READ],
) -> dict[str, Any]:
    return {"guardrails": await _service(request).list(tenant)}


@router.get("/guardrails/{bundle_id}")
async def guardrail(
    bundle_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GUARDRAIL_READ],
) -> dict[str, Any]:
    return await _service(request).summary(tenant, bundle_id)


@router.post("/guardrails/{bundle_id}/draft", status_code=201)
async def create_draft(
    bundle_id: str,
    body: DraftCreate,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.DRAFT_WRITE],
    audit: Audit = _AUDIT[AuditAction.DRAFT_CREATE],
) -> dict[str, Any]:
    draft = await _service(request).create_draft(tenant, bundle_id, body.base_version)
    audit.set(bundle_id, base_version=draft["base_version"])
    return draft


@router.get("/guardrails/{bundle_id}/draft")
async def get_draft(
    bundle_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GUARDRAIL_READ],
) -> dict[str, Any]:
    return await _service(request).get_draft(tenant, bundle_id)


@router.put("/guardrails/{bundle_id}/draft")
async def save_draft(
    bundle_id: str,
    body: DraftSave,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.DRAFT_WRITE],
    audit: Audit = _AUDIT[AuditAction.DRAFT_SAVE],
) -> dict[str, Any]:
    draft = await _service(request).save_draft(tenant, bundle_id, body.bundle)
    audit.set(bundle_id, problems=len(draft["problems"]))
    return draft


@router.delete("/guardrails/{bundle_id}/draft", status_code=204)
async def discard_draft(
    bundle_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.DRAFT_WRITE],
    audit: Audit = _AUDIT[AuditAction.DRAFT_DISCARD],
) -> None:
    await _service(request).discard_draft(tenant, bundle_id)
    audit.set(bundle_id)


@router.post("/guardrails/{bundle_id}/draft/test")
async def test_draft(
    bundle_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.DRAFT_TEST],
    audit: Audit = _AUDIT[AuditAction.DRAFT_TEST],
) -> dict[str, Any]:
    report = await _service(request).test_draft(tenant, bundle_id)
    audit.set(bundle_id, passed=report["passed"], total=report["total"], failed=report["failed"])
    return report


@router.post("/guardrails/{bundle_id}/draft/publish")
async def publish(
    bundle_id: str,
    body: PublishRequest,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GUARDRAIL_PUBLISH],
    audit: Audit = _AUDIT[AuditAction.GUARDRAIL_PUBLISH],
) -> dict[str, Any]:
    if body.activate:  # publish-and-activate also needs the activation permission
        await authorize(Action.GUARDRAIL_ACTIVATE)(request)
    result = await _service(request).publish(
        tenant,
        bundle_id,
        activate=body.activate,
        accept=body.accept,
        note=body.note,
        actor=actor.record(),
    )
    audit.set(
        bundle_id,
        version=result["version"],
        activated=result["activated"],
        previous_active=result["previous_active"],
        accepted=result["accepted"] or None,
        note=body.note,
    )
    return result


@router.post("/guardrails/{bundle_id}/activate")
async def activate(
    bundle_id: str,
    body: ActivateRequest,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GUARDRAIL_ACTIVATE],
    audit: Audit = _AUDIT[AuditAction.GUARDRAIL_ACTIVATE],
) -> dict[str, Any]:
    result = await asyncio.to_thread(
        _service(request).activate, tenant, bundle_id, body.version, body.note, actor.record()
    )
    audit.set(
        bundle_id, version=before_after(result["previous_active"], body.version), note=body.note
    )
    return result


# ------------------------------------------------------------------ records (ADR-0041)


def _utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


@router.get("/decision-events")
async def list_decision_events(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.DECISIONS_READ],
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=200),
    since: datetime | None = None,
    until: datetime | None = None,
    action: DecisionAction | None = None,
    source: Literal["api", "gateway"] | None = None,
    application_id: str | None = Query(None, max_length=64),
    phase: Phase | None = None,
    review: bool | None = None,
) -> dict[str, Any]:
    """Decision events of the tenant, newest first (tagged-action 0.4)."""

    try:
        page = await request.app.state.records.events(
            tenant,
            limit=limit,
            cursor=cursor,
            since=_utc(since),
            until=_utc(until),
            action=action,
            source=source,
            application_id=application_id,
            phase=phase,
            review=review,
        )
    except BadCursor as exc:
        raise HTTPException(400, {"error": "bad_cursor", "detail": str(exc)}) from exc
    return page.model_dump(mode="json", by_alias=True, exclude_none=True)


@router.get("/decision-events/summary")
async def summarize_decision_events(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.DECISIONS_READ],
    since: datetime | None = None,
    until: datetime | None = None,
    action: DecisionAction | None = None,
    source: Literal["api", "gateway"] | None = None,
    application_id: str | None = Query(None, max_length=64),
    phase: Phase | None = None,
    review: bool | None = None,
) -> dict[str, Any]:
    """Counts for charts over the same filters as the list (tagged-action 0.5). Declared before
    ``/decision-events/{event_id}`` so ``summary`` is not taken for an event id."""

    summary = await request.app.state.records.summary(
        tenant,
        since=_utc(since),
        until=_utc(until),
        action=action,
        source=source,
        application_id=application_id,
        phase=phase,
        review=review,
    )
    return summary.model_dump(mode="json", by_alias=True)


@router.get("/decision-events/{event_id}")
async def get_decision_event(
    event_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.DECISIONS_READ],
) -> dict[str, Any]:
    detail = await request.app.state.records.event(tenant, event_id[:64])
    if detail is None:
        raise HTTPException(404, {"error": "not_found", "detail": "no such decision event"})
    return detail.model_dump(mode="json", by_alias=True, exclude_none=True)


@router.get("/usage")
async def get_usage(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.USAGE_READ],
    month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
) -> dict[str, Any]:
    """One calendar month (UTC) of the usage ledger (usage 0.2)."""

    summary = await request.app.state.records.usage(tenant, month)
    return summary.model_dump(mode="json", by_alias=True)


def _entitlements_view(request: Request, tenant: TenantInfo, used: dict) -> EntitlementsView:
    month, _, resets_at = month_bounds(None)
    return EntitlementsView(
        tenant_id=tenant.slug,
        edition=tenant.edition,  # type: ignore[arg-type]
        limits=request.app.state.keys.plans.limits(tenant),
        month=month,
        used=RequestsUsed(requests=used["live"], test_requests=used["test"]),
        resets_at=resets_at,
    )


@router.get("/entitlements")
async def get_entitlements(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.USAGE_READ],
) -> dict[str, Any]:
    """The tenant's plan and this month's requests, counted as the engine counts them
    (entitlements 0.1, ADR-0043)."""

    _, start, end = month_bounds(None)
    used = await request.app.state.records.requests_used(tenant, start, end)
    return _entitlements_view(request, tenant, used).model_dump(mode="json")


# ------------------------------------------------------------------ audit log (ADR-0045)

_ACTION_FILTER = r"^[a-z]+(\.[a-z]+)*$"  # an audit action, or a prefix of one up to a dot


def _audit_filters(
    since: datetime | None = None,
    until: datetime | None = None,
    actor: str | None = Query(None, max_length=255),
    action: str | None = Query(None, max_length=64, pattern=_ACTION_FILTER),
    target_type: str | None = Query(None, max_length=32),
    target_id: str | None = Query(None, max_length=255),
    outcome: Literal["success", "denied"] | None = None,
) -> dict[str, Any]:
    """The filters of the list and the export (audit 0.1): ``actor`` is ``operator``, a
    person's user id or their subject."""

    return {
        "since": _utc(since),
        "until": _utc(until),
        "actor": actor,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "outcome": outcome,
    }


_AUDIT_FILTERS = Depends(_audit_filters)


@router.get("/audit-events")
async def list_audit_events(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.AUDIT_READ],
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=200),
    filters: dict[str, Any] = _AUDIT_FILTERS,
) -> dict[str, Any]:
    """The tenant's audit events, newest first (audit 0.1)."""

    try:
        page = await request.app.state.audit.page(tenant, limit=limit, cursor=cursor, **filters)
    except BadAuditCursor as exc:
        raise HTTPException(400, {"error": "bad_cursor", "detail": str(exc)}) from exc
    return page.model_dump(mode="json", exclude_none=True)


@router.get("/audit-events/export")
async def export_audit_events(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.AUDIT_READ],
    audit: Audit = _AUDIT[AuditAction.AUDIT_EXPORT],
    filters: dict[str, Any] = _AUDIT_FILTERS,
) -> StreamingResponse:
    """The matching events as CSV, newest first, at most ``EXPORT_MAX`` rows; the export itself
    is an audit event."""

    audit_log: AuditLog = request.app.state.audit
    matched = await audit_log.count(tenant, **filters)
    audit.set(rows=min(matched, EXPORT_MAX), **{k: v for k, v in filters.items() if v is not None})
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        audit_log.csv_lines(tenant, **filters),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="audit-{tenant.slug}-{stamp}.csv"',
            "X-Umai-Export-Rows": str(min(matched, EXPORT_MAX)),
            "X-Umai-Export-Truncated": "true" if matched > EXPORT_MAX else "false",
        },
    )


# ------------------------------------------------------------------ billing (ADR-0044)


def _billing(request: Request) -> BillingService:
    return request.app.state.billing


def _can_manage_billing(request: Request, actor: Actor) -> bool:
    """What ``authorize(BILLING_MANAGE)`` would answer: a person with the role (without the
    rbac entitlement an admin holds the billing role's permissions, ADR-0029 §2), or the
    operator where assertions are optional (single-operator installs)."""

    if actor.kind == "operator":
        return request.app.state.settings.actor_assertion == "optional"
    return _allowed(Action.BILLING_MANAGE, actor.roles, rbac=False)


@router.get("/billing")
async def get_billing(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.BILLING_READ],
) -> dict[str, Any]:
    """The tenant's plan, subscription, billing details and the plans on sale (billing 0.1)."""

    view = await _billing(request).view(tenant, can_manage=_can_manage_billing(request, actor))
    return view.model_dump(mode="json")


@router.put("/billing/account")
async def put_billing_account(
    body: BillingAccount,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.BILLING_MANAGE],
    audit: Audit = _AUDIT[AuditAction.BILLING_ACCOUNT_UPDATE],
) -> dict[str, Any]:
    await _billing(request).save_account(tenant, body, actor.label)
    audit.set()  # the details themselves are billing data: never in the audit log
    view = await _billing(request).view(tenant, can_manage=True)
    return view.model_dump(mode="json")


@router.post("/billing/checkout")
async def start_checkout(
    body: CheckoutRequest,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.BILLING_MANAGE],
    audit: Audit = _AUDIT[AuditAction.BILLING_CHECKOUT_START],
) -> dict[str, Any]:
    """Opens iyzico's hosted form for a paid plan; card data never reaches UMAI (D2)."""

    started = await _billing(request).start_checkout(tenant, body.edition, actor.label)
    audit.set(started.checkout_id, edition=body.edition)
    return started.model_dump(mode="json")


@router.post("/billing/checkouts/{checkout_id}/complete")
async def complete_checkout(
    checkout_id: str,
    body: CheckoutCompletion,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.BILLING_COMPLETE],
    audit: Audit = _AUDIT[AuditAction.BILLING_CHECKOUT_COMPLETE],
) -> dict[str, Any]:
    """The console's public return route, as operator: confirms the form by its token."""

    result = await _billing(request).complete_checkout(tenant, checkout_id[:64], body.token)
    audit.set(result.checkout_id, status=result.status, edition=result.edition)
    return result.model_dump(mode="json")


@router.post("/billing/plan")
async def change_billing_plan(
    body: PlanChange,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.BILLING_MANAGE],
    audit: Audit = _AUDIT[AuditAction.BILLING_PLAN_CHANGE],
) -> dict[str, Any]:
    await _billing(request).change_plan(tenant, body.edition, actor.label)
    audit.set(edition=body.edition, previous_edition=tenant.edition)
    view = await _billing(request).view(tenant, can_manage=True)
    return view.model_dump(mode="json")


@router.post("/billing/cancel")
async def cancel_billing(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.BILLING_MANAGE],
    audit: Audit = _AUDIT[AuditAction.BILLING_CANCEL],
) -> dict[str, Any]:
    await _billing(request).cancel(tenant, actor.label)
    audit.set(edition=tenant.edition)
    view = await _billing(request).view(tenant, can_manage=True)
    return view.model_dump(mode="json")


hooks = APIRouter(prefix="/hooks")  # public: no bearer token; each hook proves itself


@hooks.post("/iyzico")
async def iyzico_webhook(request: Request) -> dict[str, Any]:
    """iyzico's subscription notifications (D5): signature, dedupe, then re-read. The body is
    references only and is never logged."""

    raw = await request.body()
    if len(raw) > 8_192:
        raise HTTPException(413, {"error": "too_large", "detail": "notification too large"})
    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(400, {"error": "invalid_json", "detail": "not JSON"}) from None
    if not isinstance(payload, dict):
        raise HTTPException(400, {"error": "invalid_json", "detail": "not an object"})
    outcome = await _billing(request).handle_webhook(
        payload, request.headers.get("x-iyz-signature-v3", "")
    )
    log.info("billing.webhook outcome=%s type=%s", outcome, str(payload.get("iyziEventType"))[:40])
    return {"received": True}


# ------------------------------------------------------------------ tenants and API keys


def _tenant_repo(request: Request) -> TenantRepository:
    return request.app.state.tenants


async def _tenant(request: Request, tenant_id: str) -> TenantInfo:
    try:
        return await _tenant_repo(request).require(tenant_id)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/tenants")
async def list_tenants(
    request: Request, actor: Actor = _ALLOW[Action.TENANT_READ]
) -> dict[str, Any]:
    return {"tenants": [t.public() for t in await _tenant_repo(request).list()]}


@router.post("/tenants", status_code=201)
async def create_tenant(
    body: TenantCreate,
    request: Request,
    actor: Actor = _ALLOW[Action.TENANT_CREATE],
    audit: Audit = _AUDIT[AuditAction.TENANT_CREATE],
) -> dict[str, Any]:
    region = body.region or request.app.state.settings.region
    try:
        tenant = await _tenant_repo(request).create(body.id, body.name or body.id, region)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    audit.set(tenant.slug, tenant=tenant, name=tenant.name, region=tenant.region)
    return tenant.public()


@router.put("/tenants/{tenant_id}/plan")
async def set_plan(
    tenant_id: str,
    body: PlanUpdate,
    request: Request,
    actor: Actor = _ALLOW[Action.TENANT_PLAN],
    audit: Audit = _AUDIT[AuditAction.TENANT_PLAN],
) -> dict[str, Any]:
    """The tenant's edition and limit overrides (entitlements 0.1, ADR-0043 D3); the snapshot is
    republished so engines enforce them within one poll interval."""

    previous = await _tenant(request, tenant_id)
    try:
        tenant = await _tenant_repo(request).set_plan(
            tenant_id, body.edition, body.override_values()
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    keys = _keys(request)
    published = await keys.republish(tenant)
    log.info(
        "tenant plan set: tenant=%s edition=%s overrides=%s",
        tenant.slug,
        tenant.edition,
        sorted(tenant.entitlement_overrides or {}),
    )
    audit.set(
        tenant.slug,
        tenant=tenant,
        edition=before_after(previous.edition, tenant.edition),
        overrides=before_after(
            previous.entitlement_overrides or {}, tenant.entitlement_overrides or {}
        ),
    )
    return {
        "tenant": tenant.public(),
        "overrides": tenant.entitlement_overrides or {},
        "limits": keys.plans.limits(tenant).model_dump(mode="json"),
        "published": published,
    }


@router.get("/tenants/{tenant_id}")
async def get_tenant(
    tenant_id: str, request: Request, actor: Actor = _ALLOW[Action.TENANT_READ]
) -> dict[str, Any]:
    return (await _tenant(request, tenant_id)).public()


def _key_view(record) -> dict[str, Any]:
    return {**record.model_dump(mode="json", exclude_none=True), "status": record.status}


@router.get("/tenants/{tenant_id}/keys")
async def list_keys(
    tenant_id: str, request: Request, actor: Actor = _ALLOW[Action.KEY_READ]
) -> dict[str, Any]:
    check_tenant(actor, tenant_id)
    tenant = await _tenant(request, tenant_id)
    return {"keys": [_key_view(k) for k in await _keys(request).list(tenant)]}


@router.post("/tenants/{tenant_id}/keys", status_code=201)
async def create_key(
    tenant_id: str,
    body: KeyCreate,
    request: Request,
    actor: Actor = _ALLOW[Action.KEY_CREATE],
    audit: Audit = _AUDIT[AuditAction.KEY_CREATE],
) -> dict[str, Any]:
    """Creates a key; the secret is in the response once and never stored (ADR-0023 §2)."""

    check_tenant(actor, tenant_id)
    tenant = await _tenant(request, tenant_id)
    if body.application_id is not None:
        data = await _read_file(request, tenant)
        if not any(
            _is(a, "application_id", body.application_id) for a in _entries(data, "applications")
        ):
            raise HTTPException(
                422, {"error": "unknown_application", "detail": "no such application"}
            )
    try:
        record, secret, published = await _keys(request).create(
            tenant,
            name=body.name,
            scopes=body.scopes,
            env=body.env,
            expires_at=body.expires_at,
            created_by="control-token" if actor.kind == "operator" else actor.label,
            application_id=body.application_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit.set(  # never the secret
        record.key_id,
        tenant=tenant,
        name=record.name,
        env=record.env,
        scopes=list(record.scopes),
        application_id=record.application_id,
        expires_at=record.expires_at,
    )
    return {"key": _key_view(record), "secret": secret, "published": published}


@router.delete("/tenants/{tenant_id}/keys/{key_id}")
async def revoke_key(
    tenant_id: str,
    key_id: str,
    request: Request,
    actor: Actor = _ALLOW[Action.KEY_REVOKE],
    audit: Audit = _AUDIT[AuditAction.KEY_REVOKE],
) -> dict[str, Any]:
    check_tenant(actor, tenant_id)
    tenant = await _tenant(request, tenant_id)
    try:
        record, published = await _keys(request).revoke(tenant, key_id)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    audit.set(record.key_id, tenant=tenant, name=record.name)
    return {"key": _key_view(record), "published": published}


@router.post("/tenants/{tenant_id}/keys/publish")
async def publish_keys(
    tenant_id: str,
    request: Request,
    actor: Actor = _ALLOW[Action.KEY_PUBLISH],
    audit: Audit = _AUDIT[AuditAction.KEY_PUBLISH],
) -> dict[str, Any]:
    """Rewrite the tenant's registry snapshot (after a signing-key change, or to repair)."""

    tenant = await _tenant(request, tenant_id)
    published = await _keys(request).republish(tenant)
    audit.set(tenant.slug, tenant=tenant, published=published)
    return {"published": published}


# ------------------------------------------------------------------ organizations (ADR-0033)


def _orgs(request: Request) -> OrganizationService:
    return request.app.state.organizations


async def _org(request: Request, org_id: str, actor: Actor) -> TenantInfo:
    check_tenant(actor, org_id)
    tenant = await _tenant_repo(request).get(org_id)
    if tenant is None:
        raise HTTPException(404, {"error": "not_found", "detail": "no such organization"})
    return tenant


# ------------------------------------------- applications, providers, gateway (gateway-api 0.10)

_APPLICATION_ID = re.compile(APPLICATION_ID)
_PROVIDER_ID = re.compile(PROVIDER_ID)
# One writer per tenant file inside this process (the policy service is the only writer).
_FILE_LOCKS: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _entries(data: dict[str, Any], key: str) -> list[Any]:
    entries = data.get(key) or []
    return entries if isinstance(entries, list) else []


def _is(entry: Any, field: str, value: str) -> bool:
    return isinstance(entry, dict) and entry.get(field) == value


async def _read_file(request: Request, tenant: TenantInfo) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    return await asyncio.to_thread(read_application_file, settings.applications_dir, tenant.slug)


async def _write_file(
    request: Request, tenant: TenantInfo, data: dict[str, Any]
) -> ApplicationFile:
    """Validate the whole file as the engine and the gateway load it, then write it atomically;
    a refusal writes nothing."""

    settings: Settings = request.app.state.settings
    try:
        parsed = validate_application_file(data, AllowList(settings.gateway_upstream_allowlist))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(
            422, {"error": "invalid_configuration", "detail": application_problem(exc)}
        ) from None
    await asyncio.to_thread(write_application_file, settings.applications_dir, tenant.slug, parsed)
    return parsed


async def _body(request: Request, id_field: str, value: str) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(
            422, {"error": "invalid_configuration", "detail": "the body must be one object"}
        )
    if body.setdefault(id_field, value) != value:
        raise HTTPException(
            422, {"error": "invalid_configuration", "detail": f"{id_field}: must equal the path"}
        )
    return body


_SWITCHES = {"name", "enabled", "default"}


def _is_builtin(provider: Provider) -> bool:
    """The built-in OpenAI provider, also when only renamed, switched off or made the default."""
    return provider.model_dump(exclude=_SWITCHES) == BUILTIN_OPENAI.model_dump(exclude=_SWITCHES)


def _provider_view(content: ApplicationFile) -> list[dict[str, Any]]:
    default = content.default_provider()
    view = []
    for provider in content.effective_providers().values():
        entry = provider.model_dump(mode="json", exclude_none=True)
        entry["default"] = default is not None and provider.provider_id == default.provider_id
        entry["builtin"] = _is_builtin(provider)
        view.append(entry)
    return view


def _content(data: dict[str, Any], request: Request) -> ApplicationFile:
    settings: Settings = request.app.state.settings
    try:
        return validate_application_file(data, AllowList(settings.gateway_upstream_allowlist))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(
            409, {"error": "invalid_configuration", "detail": application_problem(exc)}
        ) from None


@router.get("/applications")
async def list_applications(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.APPLICATION_READ],
) -> dict[str, Any]:
    data = await _read_file(request, tenant)
    return {"tenant_id": tenant.slug, "applications": _entries(data, "applications")}


@router.put("/applications/{application_id}")
async def put_application(
    application_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.APPLICATION_WRITE],
    audit: Audit = _AUDIT[AuditAction.APPLICATION_SAVE],
) -> JSONResponse:
    """Create or replace one application; its guardrail must exist for this tenant. Changing
    an existing application's guardrail changes what protects its live traffic: it also needs
    ``guardrail.activate`` (ADR-0029 approver step; a person, never the control token alone)."""

    if not _APPLICATION_ID.match(application_id):
        raise HTTPException(
            422, {"error": "invalid_configuration", "detail": "application_id: not an id"}
        )
    body = await _body(request, "application_id", application_id)
    async with _FILE_LOCKS[tenant.slug]:
        data = await _read_file(request, tenant)
        apps = _entries(data, "applications")
        created = not any(_is(a, "application_id", application_id) for a in apps)
        previous = next((a for a in apps if _is(a, "application_id", application_id)), None)
        if previous is not None and previous.get("guardrail") != body.get("guardrail"):
            await authorize(Action.GUARDRAIL_ACTIVATE)(request)
        apps = [body if _is(a, "application_id", application_id) else a for a in apps]
        data["applications"] = apps + ([body] if created else [])
        guardrail = body.get("guardrail")
        known = set(_service(request).guardrails.for_tenant(tenant.slug).ids())
        if isinstance(guardrail, str) and guardrail not in known:
            raise HTTPException(
                422, {"error": "unknown_guardrail", "detail": f"no guardrail {guardrail} here"}
            )
        content = await _write_file(request, tenant, data)
    stored = content.application(application_id)
    assert stored is not None
    audit.set(
        application_id,
        created=created,
        guardrail=stored.guardrail
        if previous is None
        else before_after(previous.get("guardrail"), stored.guardrail),
    )
    log.info(
        "application.saved tenant=%s actor=%s application=%s created=%s",
        tenant.slug,
        actor.label,
        application_id,
        created,
    )
    return JSONResponse(
        {
            "tenant_id": tenant.slug,
            "application": stored.model_dump(mode="json", exclude_none=True),
        },
        status_code=201 if created else 200,
    )


@router.delete("/applications/{application_id}")
async def delete_application(
    application_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.APPLICATION_WRITE],
    audit: Audit = _AUDIT[AuditAction.APPLICATION_DELETE],
) -> dict[str, Any]:
    """Remove an application; refused while one of its keys is active, so no caller silently
    loses its guardrail."""

    async with _FILE_LOCKS[tenant.slug]:
        data = await _read_file(request, tenant)
        apps = _entries(data, "applications")
        if not any(_is(a, "application_id", application_id) for a in apps):
            raise HTTPException(
                404, {"error": "unknown_application", "detail": "no such application"}
            )
        keys = await _keys(request).list(tenant)
        active = [k for k in keys if k.application_id == application_id and k.revoked_at is None]
        if active:
            raise HTTPException(
                409,
                {
                    "error": "application_in_use",
                    "detail": f"{len(active)} active key(s) belong to it; revoke them first",
                },
            )
        data["applications"] = [a for a in apps if not _is(a, "application_id", application_id)]
        await _write_file(request, tenant, data)
    log.info(
        "application.deleted tenant=%s actor=%s application=%s",
        tenant.slug,
        actor.label,
        application_id,
    )
    audit.set(application_id)
    return {"tenant_id": tenant.slug, "deleted": application_id}


@router.get("/providers")
async def list_providers(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GATEWAY_READ],
) -> dict[str, Any]:
    """The AI gateway's providers, the built-in OpenAI provider included (``builtin: true``)."""

    content = _content(await _read_file(request, tenant), request)
    return {"tenant_id": tenant.slug, "providers": _provider_view(content)}


@router.put("/providers/{provider_id}")
async def put_provider(
    provider_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GATEWAY_WRITE],
    audit: Audit = _AUDIT[AuditAction.PROVIDER_SAVE],
) -> JSONResponse:
    """Create or replace one provider. ``default: true`` moves the default to it. Defining
    ``openai`` replaces the built-in OpenAI provider (for example to disable it)."""

    if not _PROVIDER_ID.match(provider_id):
        raise HTTPException(
            422, {"error": "invalid_configuration", "detail": "provider_id: not an id"}
        )
    body = await _body(request, "provider_id", provider_id)
    if body.get("credential") == "secret" and actor.kind != "operator":
        # Provider keys in UMAI_DE_GATEWAY_PROVIDER_KEYS belong to the deployment, not to a
        # tenant: only the operator may point a tenant's provider at one (review fix, Q47).
        raise HTTPException(
            403,
            {
                "error": "forbidden",
                "detail": "only the operator can give a provider a key the deployment holds; "
                "use credential pass_through",
            },
        )
    async with _FILE_LOCKS[tenant.slug]:
        data = await _read_file(request, tenant)
        providers = _entries(data, "providers")
        created = not any(_is(p, "provider_id", provider_id) for p in providers)
        if "default" not in body and any(
            _is(p, "provider_id", provider_id) and p.get("default") is True for p in providers
        ):
            # Re-saving the default without the flag keeps it the default: only an explicit
            # `default: false` moves it, so pass-through keys never silently go elsewhere.
            body["default"] = True
        if body.get("default") is True:
            providers = [
                {**p, "default": False} if isinstance(p, dict) and p.get("default") else p
                for p in providers
            ]
        providers = [body if _is(p, "provider_id", provider_id) else p for p in providers]
        data["providers"] = providers + ([body] if created else [])
        content = await _write_file(request, tenant, data)
    view = next(p for p in _provider_view(content) if p["provider_id"] == provider_id)
    audit.set(  # the credential mode, never a key
        provider_id,
        created=created,
        credential=view.get("credential"),
        enabled=view.get("enabled"),
        default=view.get("default"),
    )
    log.info(
        "provider.saved tenant=%s actor=%s provider=%s created=%s",
        tenant.slug,
        actor.label,
        provider_id,
        created,
    )
    return JSONResponse(
        {"tenant_id": tenant.slug, "provider": view}, status_code=201 if created else 200
    )


@router.delete("/providers/{provider_id}")
async def delete_provider(
    provider_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GATEWAY_WRITE],
    audit: Audit = _AUDIT[AuditAction.PROVIDER_DELETE],
) -> dict[str, Any]:
    """Remove a provider; refused while an application names it (allowed providers, aliases or
    fallback) or, for the provider marked default, while an application sends bare model names
    to it. Removing a defined ``openai`` brings the built-in one back."""

    async with _FILE_LOCKS[tenant.slug]:
        data = await _read_file(request, tenant)
        providers = _entries(data, "providers")
        if not any(_is(p, "provider_id", provider_id) for p in providers):
            raise HTTPException(
                404,
                {
                    "error": "unknown_provider",
                    "detail": "no such provider (the built-in OpenAI provider can be disabled, "
                    "not removed)",
                },
            )
        content = _content(data, request)
        users = sorted(
            a.application_id
            for a in content.applications
            if provider_id in a.providers
            or any(m.model.startswith(f"@{provider_id}/") for m in a.aliases)
            or (a.fallback or "").startswith(f"@{provider_id}/")
        )
        if users:
            raise HTTPException(
                409,
                {"error": "provider_in_use", "detail": f"application {users[0]} uses it"},
            )
        marked = next((p for p in content.providers if p.default), None)
        if marked is not None and marked.provider_id == provider_id:
            # Without it the built-in OpenAI provider becomes the default, and callers' keys
            # for this provider would go to api.openai.com.
            relying = sorted(a.application_id for a in content.applications if not a.providers)
            if relying:
                raise HTTPException(
                    409,
                    {
                        "error": "provider_in_use",
                        "detail": f"application {relying[0]} sends its models to it as the "
                        "default provider; mark another provider default first",
                    },
                )
        data["providers"] = [p for p in providers if not _is(p, "provider_id", provider_id)]
        await _write_file(request, tenant, data)
    log.info(
        "provider.deleted tenant=%s actor=%s provider=%s", tenant.slug, actor.label, provider_id
    )
    audit.set(provider_id)
    return {"tenant_id": tenant.slug, "deleted": provider_id}


@router.get("/gateway/settings")
async def gateway_settings(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GATEWAY_READ],
) -> dict[str, Any]:
    """The AI gateway's defaults for every application on it."""

    content = _content(await _read_file(request, tenant), request)
    return {"tenant_id": tenant.slug, "gateway": content.gateway.model_dump(mode="json")}


@router.put("/gateway/settings")
async def put_gateway_settings(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GATEWAY_WRITE],
    audit: Audit = _AUDIT[AuditAction.GATEWAY_SETTINGS_UPDATE],
) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(
            422, {"error": "invalid_configuration", "detail": "the body must be one object"}
        )
    async with _FILE_LOCKS[tenant.slug]:
        data = await _read_file(request, tenant)
        try:
            old = _content(data, request).gateway.model_dump(mode="json")
        except HTTPException:  # a stored file that no longer validates: everything changes
            old = {}
        data["gateway"] = body
        content = await _write_file(request, tenant, data)
    log.info("gateway.settings.saved tenant=%s actor=%s", tenant.slug, actor.label)
    new = content.gateway.model_dump(mode="json")
    audit.set(changed=sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k)))
    return {"tenant_id": tenant.slug, "gateway": content.gateway.model_dump(mode="json")}


@router.get("/me/organizations")
async def my_organizations(
    request: Request, actor: Actor = _ALLOW[Action.ME_ORGANIZATIONS]
) -> dict[str, Any]:
    return {"organizations": await _orgs(request).mine(actor)}


@router.post("/organizations", status_code=201)
async def create_organization(
    body: OrganizationCreate,
    request: Request,
    actor: Actor = _ALLOW[Action.ORGANIZATION_CREATE],
    audit: Audit = _AUDIT[AuditAction.ORGANIZATION_CREATE],
) -> dict[str, Any]:
    result = await _orgs(request).create(actor, body)
    audit.set(
        body.id,
        tenant=await _tenant_repo(request).get(body.id),
        name=body.name,
        region=body.region,
        starter_guardrail=result["starter_guardrail"],
    )
    return result


@router.get("/organizations/{org_id}")
async def get_organization(
    org_id: str, request: Request, actor: Actor = _ALLOW[Action.ORGANIZATION_READ]
) -> dict[str, Any]:
    return await _orgs(request).get(await _org(request, org_id, actor))


@router.patch("/organizations/{org_id}")
async def update_organization(
    org_id: str,
    body: OrganizationUpdate,
    request: Request,
    actor: Actor = _ALLOW[Action.ORGANIZATION_UPDATE],
    audit: Audit = _AUDIT[AuditAction.ORGANIZATION_UPDATE],
) -> dict[str, Any]:
    tenant = await _org(request, org_id, actor)
    result = await _orgs(request).rename(tenant, body.name)
    audit.set(tenant.slug, tenant=tenant, name=before_after(tenant.name, body.name))
    return result


@router.get("/organizations/{org_id}/members")
async def list_members(
    org_id: str, request: Request, actor: Actor = _ALLOW[Action.MEMBER_READ]
) -> dict[str, Any]:
    return {"members": await _orgs(request).members(await _org(request, org_id, actor))}


@router.patch("/organizations/{org_id}/members/{user_id}")
async def update_member(
    org_id: str,
    user_id: uuid.UUID,
    body: MembershipUpdate,
    request: Request,
    actor: Actor = _ALLOW[Action.MEMBER_UPDATE],
    audit: Audit = _AUDIT[AuditAction.MEMBER_UPDATE],
) -> dict[str, Any]:
    tenant = await _org(request, org_id, actor)
    member, before = await _orgs(request).set_roles(tenant, actor, user_id, body.roles)
    audit.set(user_id, tenant=tenant, roles=before_after(before, member["roles"]))
    return member


@router.delete("/organizations/{org_id}/members/{user_id}", status_code=204)
async def remove_member(
    org_id: str,
    user_id: uuid.UUID,
    request: Request,
    actor: Actor = _ALLOW[Action.MEMBER_REMOVE],
    audit: Audit = _AUDIT[AuditAction.MEMBER_REMOVE],
) -> None:
    tenant = await _org(request, org_id, actor)
    before = await _orgs(request).remove(tenant, actor, user_id)
    audit.set(user_id, tenant=tenant, roles=before_after(before, None))


@router.post("/organizations/{org_id}/leave", status_code=204)
async def leave_organization(
    org_id: str,
    request: Request,
    actor: Actor = _ALLOW[Action.MEMBER_LEAVE],
    audit: Audit = _AUDIT[AuditAction.MEMBER_LEAVE],
) -> None:
    tenant = await _org(request, org_id, actor)
    await _orgs(request).leave(tenant, actor)
    user = await _orgs(request).users.get(actor.sub or "")
    audit.set(user.id if user else None, tenant=tenant)


@router.get("/organizations/{org_id}/invitations")
async def list_invitations(
    org_id: str, request: Request, actor: Actor = _ALLOW[Action.INVITATION_MANAGE]
) -> dict[str, Any]:
    tenant = await _org(request, org_id, actor)
    return {"invitations": await _orgs(request).list_invitations(tenant)}


@router.post("/organizations/{org_id}/invitations", status_code=201)
async def create_invitation(
    org_id: str,
    body: InvitationCreate,
    request: Request,
    actor: Actor = _ALLOW[Action.INVITATION_MANAGE],
    audit: Audit = _AUDIT[AuditAction.INVITATION_CREATE],
) -> dict[str, Any]:
    """The link's secret is in the response once (``token``) and stored as a hash only."""

    tenant = await _org(request, org_id, actor)
    result = await _orgs(request).invite(tenant, actor, body)
    invitation = result["invitation"]  # never the token, nor the invitee's address
    audit.set(
        invitation["id"],
        tenant=tenant,
        roles=invitation["roles"],
        expires_at=invitation["expires_at"],
    )
    return result


@router.delete("/organizations/{org_id}/invitations/{invitation_id}")
async def revoke_invitation(
    org_id: str,
    invitation_id: uuid.UUID,
    request: Request,
    actor: Actor = _ALLOW[Action.INVITATION_MANAGE],
    audit: Audit = _AUDIT[AuditAction.INVITATION_REVOKE],
) -> dict[str, Any]:
    tenant = await _org(request, org_id, actor)
    result = await _orgs(request).revoke(tenant, invitation_id)
    audit.set(invitation_id, tenant=tenant)
    return result


@router.post("/organizations/{org_id}/invitations/{invitation_id}/resend", status_code=201)
async def resend_invitation(
    org_id: str,
    invitation_id: uuid.UUID,
    request: Request,
    actor: Actor = _ALLOW[Action.INVITATION_MANAGE],
    audit: Audit = _AUDIT[AuditAction.INVITATION_RESEND],
) -> dict[str, Any]:
    tenant = await _org(request, org_id, actor)
    result = await _orgs(request).resend(tenant, actor, invitation_id)
    audit.set(invitation_id, tenant=tenant, replaced_by=result["invitation"]["id"])
    return result


@router.post("/invitations/accept")
async def accept_invitation(
    body: InvitationAccept,
    request: Request,
    actor: Actor = _ALLOW[Action.INVITATION_ACCEPT],
    audit: Audit = _AUDIT[AuditAction.INVITATION_ACCEPT],
) -> dict[str, Any]:
    result = await _orgs(request).accept(actor, body.organization, body.token)
    user = await _orgs(request).users.get(actor.sub or "")
    audit.set(
        user.id if user else None,
        tenant=await _tenant_repo(request).get(body.organization),
        roles=result["roles"],
    )
    return result


def create_app(
    settings: Settings | None = None,
    *,
    detector: EntityDetector | None = None,
    billing_transport: httpx.AsyncBaseTransport | None = None,  # tests: a fake iyzico
    billing_clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg = settings or get_settings()
        if cfg.env != "development" and cfg.control_api_token is None:
            raise RuntimeError("UMAI_DE_CONTROL_API_TOKEN is required outside development")
        configure_logging(cfg.log_level)
        signing_key = None
        if cfg.signing_key_file and cfg.signing_key_id:
            private = cfg.signing_key_file.read_text(encoding="utf-8").strip()
            base64.b64decode(private, validate=True)  # fail at start, not at publish
            signing_key = (cfg.signing_key_id, private)
        own_detector = detector is None
        engine = detector or EntityDetector(lambda _tenant: _TESTS_KEY, threads=1)
        if own_detector:
            await asyncio.to_thread(engine.warm_up)
        # Tenant store (ADR-0025): migrations with the owner URL, requests with the app role.
        url = cfg.database_url.get_secret_value() if cfg.database_url else None
        if url is None:
            cfg.control_db_path.parent.mkdir(parents=True, exist_ok=True)
            url = sqlite_url(cfg.control_db_path)
        admin_url = cfg.database_admin_url.get_secret_value() if cfg.database_admin_url else url
        await asyncio.to_thread(upgrade, admin_url)
        db = make_engine(url)
        tenants = TenantRepository(db)
        await tenants.get_or_create(cfg.default_tenant_id, cfg.region)
        app.state.settings = cfg
        app.state.db = db
        app.state.tenants = tenants
        app.state.keys = KeyService(
            ApiKeyRepository(db),
            KeyPublisher(cfg.key_registry_dir, signing_key),
            cfg.region,
            Plans.load(cfg.plans_file),
        )
        # A changed plans file reaches the engines with the next snapshots (ADR-0043 D3).
        republished = await app.state.keys.republish_published(await tenants.list())
        log.info("control_plane.snapshots republished=%d", republished)
        app.state.actor_keys = {
            kid: load_public_key(value) for kid, value in cfg.actor_public_keys.items()
        }
        log.info(
            "control_plane.actor_assertion mode=%s keys=%d%s",
            cfg.actor_assertion,
            len(app.state.actor_keys),
            "" if cfg.actor_assertion == "required" else " (the control token acts as operator)",
        )
        app.state.service = PolicyService(
            GuardrailRepositories(cfg.policy_dir, cfg.default_tenant_id),
            DraftRepository(db),
            engine,
            signing_key,
        )
        service = app.state.service
        app.state.organizations = OrganizationService(
            tenants,
            UserRepository(db),
            MembershipRepository(db),
            InvitationRepository(db),
            region=cfg.region,
            default_tenant=cfg.default_tenant_id,
            max_owned_community=cfg.max_owned_community_organizations,
            max_pending_invitations=cfg.max_pending_invitations,
            disposable_domains=DISPOSABLE_EMAIL_DOMAINS
            | {d.strip().lower() for d in cfg.disposable_email_domains},
            starter=(
                (
                    lambda tenant, actor: service.install_starter(
                        tenant, cfg.starter_guardrail, actor
                    )
                )
                if cfg.starter_guardrail
                else None
            ),
        )
        app.state.records = RecordsReader(db)
        app.state.audit = AuditLog(db)
        # Billing (ADR-0044): iyzico when configured; plans on offer checked against it.
        client = None
        if cfg.billing_provider == "iyzico":
            if not (cfg.iyzico_api_key and cfg.iyzico_secret_key and cfg.iyzico_merchant_id):
                raise RuntimeError(
                    "UMAI_DE_BILLING_PROVIDER=iyzico needs UMAI_DE_IYZICO_API_KEY, "
                    "UMAI_DE_IYZICO_SECRET_KEY and UMAI_DE_IYZICO_MERCHANT_ID"
                )
            client = IyzicoClient(
                cfg.iyzico_base_url,
                cfg.iyzico_api_key.get_secret_value(),
                cfg.iyzico_secret_key.get_secret_value(),
                timeout_s=cfg.iyzico_timeout_s,
                transport=billing_transport,
            )
        app.state.billing = BillingService(
            db,
            tenants,
            app.state.keys,
            client=client,
            merchant_id=cfg.iyzico_merchant_id,
            secret_key=cfg.iyzico_secret_key.get_secret_value() if cfg.iyzico_secret_key else None,
            return_url=cfg.billing_return_url,
            grace_days=cfg.billing_grace_days,
            retry_days=cfg.billing_retry_days,
            plan_refs=cfg.billing_plan_refs,
            **({"clock": billing_clock} if billing_clock else {}),
        )
        await app.state.billing.verify_plans()
        reconcile = None
        if client is not None and cfg.billing_reconcile_interval_s > 0:
            reconcile = asyncio.create_task(app.state.billing.run(cfg.billing_reconcile_interval_s))
        # Retention (ADR-0041 §5) with the owner role: the application role cannot delete records.
        retention = None
        if cfg.records_retention_interval_s > 0:
            owner_db = make_engine(admin_url)
            retention = asyncio.create_task(
                retention_loop(
                    owner_db,
                    decision_days=cfg.records_decision_retention_days,
                    usage_days=cfg.records_usage_retention_days,
                    audit_days=cfg.audit_retention_days,
                    interval_s=cfg.records_retention_interval_s,
                )
            )
        try:
            yield
        finally:
            if reconcile is not None:
                reconcile.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reconcile
            if client is not None:
                await client.aclose()
            if retention is not None:
                retention.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await retention
                await owner_db.dispose()
            await db.dispose()
            if own_detector:
                engine.close()

    app = FastAPI(title="umai-policy-service", version=__version__, lifespan=lifespan)

    @app.exception_handler(ServiceError)
    async def _service_error(_request: Request, exc: ServiceError) -> JSONResponse:
        body: dict[str, Any] = {"detail": str(exc)}
        if exc.details is not None:
            body["details"] = exc.details
        return JSONResponse(body, status_code=exc.status)

    @app.exception_handler(BillingError)
    async def _billing_error(_request: Request, exc: BillingError) -> JSONResponse:
        return JSONResponse(
            {"detail": {"error": exc.code, "detail": exc.detail}}, status_code=exc.status
        )

    @app.exception_handler(OrganizationError)
    async def _organization_error(_request: Request, exc: OrganizationError) -> JSONResponse:
        return JSONResponse(
            {"detail": {"error": exc.code, "detail": str(exc)}}, status_code=exc.status
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    app.include_router(hooks)
    return app


app = create_app()
