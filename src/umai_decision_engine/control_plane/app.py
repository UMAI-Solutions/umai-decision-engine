"""Policy service HTTP API (ADR-0022).

uvicorn umai_decision_engine.control_plane.app:app --port 9200
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from umai_decision_engine import __version__
from umai_decision_engine.contracts.credentials import KeyEnv, Scope
from umai_decision_engine.contracts.organizations import (
    InvitationAccept,
    InvitationCreate,
    MembershipUpdate,
    OrganizationCreate,
    OrganizationUpdate,
)
from umai_decision_engine.control_plane.actors import load_public_key
from umai_decision_engine.control_plane.authz import (
    Action,
    Actor,
    actor_of,
    authorize,
    check_tenant,
)
from umai_decision_engine.control_plane.keys import KeyPublisher, KeyService
from umai_decision_engine.control_plane.organizations import (
    DISPOSABLE_EMAIL_DOMAINS,
    OrganizationError,
    OrganizationService,
)
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
from umai_decision_engine.routefiles import (
    AllowList,
    read_route_file,
    validate_route_file,
    write_route_file,
)
from umai_decision_engine.settings import Settings, get_settings
from umai_decision_engine.telemetry.logging import configure_logging

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
    try:
        record, secret, published = await _keys(request).create(
            tenant,
            name=body.name,
            scopes=body.scopes,
            env=body.env,
            expires_at=body.expires_at,
            created_by="control-token" if actor.kind == "operator" else actor.label,
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


# ------------------------------------------------------------- gateway routes (gateway-api 0.7)


def _route_problem(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first.get("loc", ())) or "routes"
        return f"{location}: {first.get('msg', first.get('type', 'invalid'))}"[:300]
    return str(exc)[:300]


@router.get("/gateway/routes")
async def gateway_routes(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GATEWAY_ROUTE_READ],
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    data = await asyncio.to_thread(read_route_file, settings.gateway_routes_dir, tenant.slug)
    return {"tenant_id": tenant.slug, "routes": data.get("routes") or []}


@router.put("/gateway/routes")
async def put_gateway_routes(
    request: Request,
    tenant: TenantInfo = _TENANT,
    actor: Actor = _ALLOW[Action.GATEWAY_ROUTE_WRITE],
) -> dict[str, Any]:
    """Replace the tenant's routes. Validated like the gateway loads them (schema, fallbacks,
    upstream allow-list) plus: every guardrail exists for this tenant. The gateway picks the file
    up at its next reload (seconds)."""

    settings: Settings = request.app.state.settings
    try:
        body = await request.json()
        routes = validate_route_file(body, AllowList(settings.gateway_upstream_allowlist))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(
            422, {"error": "invalid_routes", "detail": _route_problem(exc)}
        ) from None
    known = set(_service(request).guardrails.for_tenant(tenant.slug).ids())
    unknown = sorted({r.guardrail for r in routes.routes} - known)
    if unknown:
        raise HTTPException(
            422,
            {"error": "unknown_guardrail", "detail": f"no guardrail {', '.join(unknown)} here"},
        )
    await asyncio.to_thread(write_route_file, settings.gateway_routes_dir, tenant.slug, routes)
    log.info(
        "gateway.routes.updated tenant=%s actor=%s routes=%d",
        tenant.slug,
        actor.label,
        len(routes.routes),
    )
    return {
        "tenant_id": tenant.slug,
        "routes": routes.model_dump(mode="json", exclude_none=True)["routes"],
    }


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
    settings: Settings | None = None, *, detector: EntityDetector | None = None
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
            ApiKeyRepository(db), KeyPublisher(cfg.key_registry_dir, signing_key), cfg.region
        )
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
        try:
            yield
        finally:
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

    @app.exception_handler(OrganizationError)
    async def _organization_error(_request: Request, exc: OrganizationError) -> JSONResponse:
        return JSONResponse(
            {"detail": {"error": exc.code, "detail": str(exc)}}, status_code=exc.status
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app


app = create_app()
