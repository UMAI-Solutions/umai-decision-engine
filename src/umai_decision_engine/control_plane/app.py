"""Policy service HTTP API (ADR-0022).

uvicorn umai_decision_engine.control_plane.app:app --port 9200
"""

from __future__ import annotations

import asyncio
import base64
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from umai_decision_engine import __version__
from umai_decision_engine.contracts.credentials import KeyEnv, Scope
from umai_decision_engine.control_plane.keys import KeyPublisher, KeyService
from umai_decision_engine.control_plane.repository import GuardrailRepository
from umai_decision_engine.control_plane.service import PolicyService, ServiceError
from umai_decision_engine.db.migrate import upgrade
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
    """The tenant a control-plane request acts for: the ``X-Umai-Tenant`` header, else the
    configured default tenant. The operator credential (control token) may act for any tenant;
    per-person authorisation arrives with the console login (ADR-0024, ADR-0029)."""

    slug = request.headers.get(TENANT_HEADER) or request.app.state.settings.default_tenant_id
    tenant = await request.app.state.tenants.get(slug)
    if tenant is None:
        raise HTTPException(404, f"tenant {slug} does not exist")
    return tenant


router = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])
_TENANT = Depends(current_tenant)


def _service(request: Request) -> PolicyService:
    return request.app.state.service


def _keys(request: Request) -> KeyService:
    return request.app.state.keys


@router.get("/catalog")
async def catalog() -> dict[str, Any]:
    return {
        "entities": [
            {"type": entity, "tag": TAG_BY_ENTITY.get(entity), "group": _catalog_group(entity)}
            for entity in sorted(SPECS_BY_ENTITY)
        ]
    }


@router.get("/guardrails")
async def list_guardrails(request: Request, tenant: TenantInfo = _TENANT) -> dict[str, Any]:
    return {"guardrails": await _service(request).list(tenant)}


@router.get("/guardrails/{bundle_id}")
async def guardrail(
    bundle_id: str, request: Request, tenant: TenantInfo = _TENANT
) -> dict[str, Any]:
    return await _service(request).summary(tenant, bundle_id)


@router.post("/guardrails/{bundle_id}/draft", status_code=201)
async def create_draft(
    bundle_id: str, body: DraftCreate, request: Request, tenant: TenantInfo = _TENANT
) -> dict[str, Any]:
    return await _service(request).create_draft(tenant, bundle_id, body.base_version)


@router.get("/guardrails/{bundle_id}/draft")
async def get_draft(
    bundle_id: str, request: Request, tenant: TenantInfo = _TENANT
) -> dict[str, Any]:
    return await _service(request).get_draft(tenant, bundle_id)


@router.put("/guardrails/{bundle_id}/draft")
async def save_draft(
    bundle_id: str, body: DraftSave, request: Request, tenant: TenantInfo = _TENANT
) -> dict[str, Any]:
    return await _service(request).save_draft(tenant, bundle_id, body.bundle)


@router.delete("/guardrails/{bundle_id}/draft", status_code=204)
async def discard_draft(bundle_id: str, request: Request, tenant: TenantInfo = _TENANT) -> None:
    await _service(request).discard_draft(tenant, bundle_id)


@router.post("/guardrails/{bundle_id}/draft/test")
async def test_draft(
    bundle_id: str, request: Request, tenant: TenantInfo = _TENANT
) -> dict[str, Any]:
    return await _service(request).test_draft(tenant, bundle_id)


@router.post("/guardrails/{bundle_id}/draft/publish")
async def publish(
    bundle_id: str, body: PublishRequest, request: Request, tenant: TenantInfo = _TENANT
) -> dict[str, Any]:
    return await _service(request).publish(
        tenant, bundle_id, activate=body.activate, accept=body.accept, note=body.note
    )


@router.post("/guardrails/{bundle_id}/activate")
async def activate(bundle_id: str, body: ActivateRequest, request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(_service(request).activate, bundle_id, body.version, body.note)


# ------------------------------------------------------------------ tenants and API keys


def _tenant_repo(request: Request) -> TenantRepository:
    return request.app.state.tenants


async def _tenant(request: Request, tenant_id: str) -> TenantInfo:
    try:
        return await _tenant_repo(request).require(tenant_id)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/tenants")
async def list_tenants(request: Request) -> dict[str, Any]:
    return {"tenants": [t.public() for t in await _tenant_repo(request).list()]}


@router.post("/tenants", status_code=201)
async def create_tenant(body: TenantCreate, request: Request) -> dict[str, Any]:
    region = body.region or request.app.state.settings.region
    try:
        tenant = await _tenant_repo(request).create(body.id, body.name or body.id, region)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return tenant.public()


@router.get("/tenants/{tenant_id}")
async def get_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    return (await _tenant(request, tenant_id)).public()


def _key_view(record) -> dict[str, Any]:
    return {**record.model_dump(mode="json", exclude_none=True), "status": record.status}


@router.get("/tenants/{tenant_id}/keys")
async def list_keys(tenant_id: str, request: Request) -> dict[str, Any]:
    tenant = await _tenant(request, tenant_id)
    return {"keys": [_key_view(k) for k in await _keys(request).list(tenant)]}


@router.post("/tenants/{tenant_id}/keys", status_code=201)
async def create_key(tenant_id: str, body: KeyCreate, request: Request) -> dict[str, Any]:
    """Creates a key; the secret is in the response once and never stored (ADR-0023 §2)."""

    tenant = await _tenant(request, tenant_id)
    try:
        record, secret, published = await _keys(request).create(
            tenant,
            name=body.name,
            scopes=body.scopes,
            env=body.env,
            expires_at=body.expires_at,
            created_by="control-token",
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"key": _key_view(record), "secret": secret, "published": published}


@router.delete("/tenants/{tenant_id}/keys/{key_id}")
async def revoke_key(tenant_id: str, key_id: str, request: Request) -> dict[str, Any]:
    tenant = await _tenant(request, tenant_id)
    try:
        record, published = await _keys(request).revoke(tenant, key_id)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"key": _key_view(record), "published": published}


@router.post("/tenants/{tenant_id}/keys/publish")
async def publish_keys(tenant_id: str, request: Request) -> dict[str, Any]:
    """Rewrite the tenant's registry snapshot (after a signing-key change, or to repair)."""

    tenant = await _tenant(request, tenant_id)
    return {"published": await _keys(request).republish(tenant)}


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
        app.state.service = PolicyService(
            GuardrailRepository(cfg.policy_dir),
            DraftRepository(db),
            engine,
            signing_key,
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

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app


app = create_app()
