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
from fastapi.responses import JSONResponse
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
from umai_decision_engine.contracts.webhooks import (
    EndpointCreate,
    EndpointTestResult,
    EndpointUpdate,
)
from umai_decision_engine.control_plane.actors import load_public_key
from umai_decision_engine.control_plane.authz import (
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
from umai_decision_engine.control_plane.webhooks import WebhookConfig, WebhookError, WebhookService
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
from umai_decision_engine.webhooks.guard import GuardedTransport, Resolver, system_resolver
from umai_decision_engine.webhooks.sender import WebhookSender

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


TENANT_HEADER = "X-Umai-Tenant"


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


router = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])
_TENANT = Depends(current_tenant)
# One dependency per action of the permission matrix (ADR-0029 §3); every route takes one.
_ALLOW = {action: Depends(authorize(action)) for action in Action}


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
) -> dict[str, Any]:
    return await _service(request).create_draft(tenant, bundle_id, body.base_version)


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
) -> dict[str, Any]:
    return await _service(request).save_draft(tenant, bundle_id, body.bundle)


@router.delete("/guardrails/{bundle_id}/draft", status_code=204)
async def discard_draft(
    bundle_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.DRAFT_WRITE],
) -> None:
    await _service(request).discard_draft(tenant, bundle_id)


@router.post("/guardrails/{bundle_id}/draft/test")
async def test_draft(
    bundle_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.DRAFT_TEST],
) -> dict[str, Any]:
    return await _service(request).test_draft(tenant, bundle_id)


@router.post("/guardrails/{bundle_id}/draft/publish")
async def publish(
    bundle_id: str,
    body: PublishRequest,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GUARDRAIL_PUBLISH],
) -> dict[str, Any]:
    if body.activate:  # publish-and-activate also needs the activation permission
        await authorize(Action.GUARDRAIL_ACTIVATE)(request)
    return await _service(request).publish(
        tenant,
        bundle_id,
        activate=body.activate,
        accept=body.accept,
        note=body.note,
        actor=actor.record(),
    )


@router.post("/guardrails/{bundle_id}/activate")
async def activate(
    bundle_id: str,
    body: ActivateRequest,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GUARDRAIL_ACTIVATE],
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _service(request).activate, tenant, bundle_id, body.version, body.note, actor.record()
    )


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
) -> dict[str, Any]:
    await _billing(request).save_account(tenant, body, actor.label)
    view = await _billing(request).view(tenant, can_manage=True)
    return view.model_dump(mode="json")


@router.post("/billing/checkout")
async def start_checkout(
    body: CheckoutRequest,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.BILLING_MANAGE],
) -> dict[str, Any]:
    """Opens iyzico's hosted form for a paid plan; card data never reaches UMAI (D2)."""

    started = await _billing(request).start_checkout(tenant, body.edition, actor.label)
    return started.model_dump(mode="json")


@router.post("/billing/checkouts/{checkout_id}/complete")
async def complete_checkout(
    checkout_id: str,
    body: CheckoutCompletion,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.BILLING_COMPLETE],
) -> dict[str, Any]:
    """The console's public return route, as operator: confirms the form by its token."""

    result = await _billing(request).complete_checkout(tenant, checkout_id[:64], body.token)
    return result.model_dump(mode="json")


@router.post("/billing/plan")
async def change_billing_plan(
    body: PlanChange,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.BILLING_MANAGE],
) -> dict[str, Any]:
    await _billing(request).change_plan(tenant, body.edition, actor.label)
    view = await _billing(request).view(tenant, can_manage=True)
    return view.model_dump(mode="json")


@router.post("/billing/cancel")
async def cancel_billing(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.BILLING_MANAGE],
) -> dict[str, Any]:
    await _billing(request).cancel(tenant, actor.label)
    view = await _billing(request).view(tenant, can_manage=True)
    return view.model_dump(mode="json")


# ------------------------------------------------------------------ webhooks (ADR-0048)


def _webhooks(request: Request) -> WebhookService:
    return request.app.state.webhooks


@router.get("/webhooks")
async def get_webhooks(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.WEBHOOK_READ],
) -> dict[str, Any]:
    """The organization's endpoints, URLs masked (webhooks 0.1)."""

    return (await _webhooks(request).view(tenant)).model_dump(mode="json")


@router.post("/webhooks/endpoints", status_code=201)
async def create_webhook_endpoint(
    body: EndpointCreate,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.WEBHOOK_MANAGE],
) -> dict[str, Any]:
    """The signing secret is in this answer once (generic endpoints); it is never stored."""

    created = await _webhooks(request).create(tenant, body, actor.label)
    return created.model_dump(mode="json")


@router.get("/webhooks/endpoints/{endpoint_id}")
async def get_webhook_endpoint(
    endpoint_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.WEBHOOK_READ],
) -> dict[str, Any]:
    endpoint = await _webhooks(request).endpoint(tenant, endpoint_id[:64])
    return {"endpoint": endpoint.model_dump(mode="json")}


@router.patch("/webhooks/endpoints/{endpoint_id}")
async def update_webhook_endpoint(
    endpoint_id: str,
    body: EndpointUpdate,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.WEBHOOK_MANAGE],
) -> dict[str, Any]:
    endpoint = await _webhooks(request).update(tenant, endpoint_id[:64], body, actor.label)
    return {"endpoint": endpoint.model_dump(mode="json")}


@router.delete("/webhooks/endpoints/{endpoint_id}", status_code=204)
async def delete_webhook_endpoint(
    endpoint_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.WEBHOOK_MANAGE],
) -> None:
    await _webhooks(request).delete(tenant, endpoint_id[:64], actor.label)


@router.post("/webhooks/endpoints/{endpoint_id}/secret")
async def rotate_webhook_secret(
    endpoint_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.WEBHOOK_MANAGE],
) -> dict[str, Any]:
    """A new signing secret, once; the previous one keeps signing for 24 hours."""

    rotated = await _webhooks(request).rotate(tenant, endpoint_id[:64], actor.label)
    return rotated.model_dump(mode="json")


@router.post("/webhooks/endpoints/{endpoint_id}/test")
async def test_webhook_endpoint(
    endpoint_id: str,
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.WEBHOOK_MANAGE],
) -> dict[str, Any]:
    """Sends one ``webhook.test`` event now and answers with the attempt."""

    delivery = await _webhooks(request).test(tenant, endpoint_id[:64], actor.label)
    return EndpointTestResult(delivery=delivery).model_dump(mode="json")


@router.get("/webhooks/deliveries")
async def list_webhook_deliveries(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.WEBHOOK_READ],
    endpoint_id: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=200),
) -> dict[str, Any]:
    """The delivery log, newest first: status codes and error codes, never answer bodies."""

    page = await _webhooks(request).deliveries(
        tenant, endpoint_id=endpoint_id, limit=limit, cursor=cursor
    )
    return page.model_dump(mode="json")


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
    body: TenantCreate, request: Request, actor: Actor = _ALLOW[Action.TENANT_CREATE]
) -> dict[str, Any]:
    region = body.region or request.app.state.settings.region
    try:
        tenant = await _tenant_repo(request).create(body.id, body.name or body.id, region)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return tenant.public()


@router.put("/tenants/{tenant_id}/plan")
async def set_plan(
    tenant_id: str,
    body: PlanUpdate,
    request: Request,
    actor: Actor = _ALLOW[Action.TENANT_PLAN],
) -> dict[str, Any]:
    """The tenant's edition and limit overrides (entitlements 0.1, ADR-0043 D3); the snapshot is
    republished so engines enforce them within one poll interval."""

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
    return {"key": _key_view(record), "secret": secret, "published": published}


@router.delete("/tenants/{tenant_id}/keys/{key_id}")
async def revoke_key(
    tenant_id: str,
    key_id: str,
    request: Request,
    actor: Actor = _ALLOW[Action.KEY_REVOKE],
) -> dict[str, Any]:
    check_tenant(actor, tenant_id)
    tenant = await _tenant(request, tenant_id)
    try:
        record, published = await _keys(request).revoke(tenant, key_id)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"key": _key_view(record), "published": published}


@router.post("/tenants/{tenant_id}/keys/publish")
async def publish_keys(
    tenant_id: str, request: Request, actor: Actor = _ALLOW[Action.KEY_PUBLISH]
) -> dict[str, Any]:
    """Rewrite the tenant's registry snapshot (after a signing-key change, or to repair)."""

    tenant = await _tenant(request, tenant_id)
    return {"published": await _keys(request).republish(tenant)}


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
        data["gateway"] = body
        content = await _write_file(request, tenant, data)
    log.info("gateway.settings.saved tenant=%s actor=%s", tenant.slug, actor.label)
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
) -> dict[str, Any]:
    return await _orgs(request).create(actor, body)


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
) -> dict[str, Any]:
    return await _orgs(request).rename(await _org(request, org_id, actor), body.name)


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
) -> dict[str, Any]:
    tenant = await _org(request, org_id, actor)
    return await _orgs(request).set_roles(tenant, actor, user_id, body.roles)


@router.delete("/organizations/{org_id}/members/{user_id}", status_code=204)
async def remove_member(
    org_id: str,
    user_id: uuid.UUID,
    request: Request,
    actor: Actor = _ALLOW[Action.MEMBER_REMOVE],
) -> None:
    await _orgs(request).remove(await _org(request, org_id, actor), actor, user_id)


@router.post("/organizations/{org_id}/leave", status_code=204)
async def leave_organization(
    org_id: str, request: Request, actor: Actor = _ALLOW[Action.MEMBER_LEAVE]
) -> None:
    await _orgs(request).leave(await _org(request, org_id, actor), actor)


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
) -> dict[str, Any]:
    """The link's secret is in the response once (``token``) and stored as a hash only."""

    return await _orgs(request).invite(await _org(request, org_id, actor), actor, body)


@router.delete("/organizations/{org_id}/invitations/{invitation_id}")
async def revoke_invitation(
    org_id: str,
    invitation_id: uuid.UUID,
    request: Request,
    actor: Actor = _ALLOW[Action.INVITATION_MANAGE],
) -> dict[str, Any]:
    return await _orgs(request).revoke(await _org(request, org_id, actor), invitation_id)


@router.post("/organizations/{org_id}/invitations/{invitation_id}/resend", status_code=201)
async def resend_invitation(
    org_id: str,
    invitation_id: uuid.UUID,
    request: Request,
    actor: Actor = _ALLOW[Action.INVITATION_MANAGE],
) -> dict[str, Any]:
    tenant = await _org(request, org_id, actor)
    return await _orgs(request).resend(tenant, actor, invitation_id)


@router.post("/invitations/accept")
async def accept_invitation(
    body: InvitationAccept, request: Request, actor: Actor = _ALLOW[Action.INVITATION_ACCEPT]
) -> dict[str, Any]:
    return await _orgs(request).accept(actor, body.organization, body.token)


def create_app(
    settings: Settings | None = None,
    *,
    detector: EntityDetector | None = None,
    billing_transport: httpx.AsyncBaseTransport | None = None,  # tests: a fake iyzico
    billing_clock: Callable[[], datetime] | None = None,
    webhook_transport: httpx.AsyncBaseTransport | None = None,  # tests: a fake receiver
    webhook_resolver: Resolver | None = None,  # tests: a fake DNS
    webhook_clock: Callable[[], datetime] | None = None,
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
        # Webhooks (ADR-0048): a guarded sender and the worker, when the deployment has a key.
        webhook_key = cfg.webhook_key()
        resolver = webhook_resolver or system_resolver
        sender = None
        if webhook_key is not None:
            sender = WebhookSender(
                webhook_transport
                or GuardedTransport(resolver, connect_timeout_s=cfg.webhooks_connect_timeout_s),
                connect_timeout_s=cfg.webhooks_connect_timeout_s,
                timeout_s=cfg.webhooks_timeout_s,
            )
        app.state.webhooks = WebhookService(
            db,
            tenants,
            app.state.keys.plans,
            master_key=webhook_key,
            sender=sender,
            config=WebhookConfig.from_settings(cfg),
            resolver=resolver,
            **({"clock": webhook_clock} if webhook_clock else {}),
        )
        log.info(
            "control_plane.webhooks available=%s active_tenants=%d",
            app.state.webhooks.available,
            await app.state.webhooks.rebuild(),
        )
        webhook_worker = None
        if sender is not None and cfg.webhooks_interval_s > 0:
            webhook_worker = asyncio.create_task(app.state.webhooks.run(cfg.webhooks_interval_s))
        # Retention (ADR-0041 §5) with the owner role: the application role cannot delete records.
        retention = None
        if cfg.records_retention_interval_s > 0:
            owner_db = make_engine(admin_url)
            retention = asyncio.create_task(
                retention_loop(
                    owner_db,
                    decision_days=cfg.records_decision_retention_days,
                    usage_days=cfg.records_usage_retention_days,
                    interval_s=cfg.records_retention_interval_s,
                    webhook_days=cfg.webhooks_retention_days,
                )
            )
        try:
            yield
        finally:
            for task in (reconcile, webhook_worker):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            if client is not None:
                await client.aclose()
            if sender is not None:
                await sender.aclose()
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

    @app.exception_handler(WebhookError)
    async def _webhook_error(_request: Request, exc: WebhookError) -> JSONResponse:
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
