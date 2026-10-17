"""Policy service HTTP API (ADR-0022).

uvicorn umai_decision_engine.control_plane.app:app --port 9200
"""

from __future__ import annotations

import asyncio
import base64
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from umai_decision_engine import __version__
from umai_decision_engine.control_plane.drafts import DraftStore
from umai_decision_engine.control_plane.repository import GuardrailRepository
from umai_decision_engine.control_plane.service import PolicyService, ServiceError
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


async def require_token(request: Request) -> None:
    token = request.app.state.settings.control_api_token
    if token is None:
        return  # development only; the app refuses to start without one elsewhere
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        value.encode(), token.get_secret_value().encode()
    ):
        raise HTTPException(401, "missing or invalid token", headers={"WWW-Authenticate": "Bearer"})


router = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])


def _service(request: Request) -> PolicyService:
    return request.app.state.service


@router.get("/catalog")
async def catalog() -> dict[str, Any]:
    return {
        "entities": [
            {"type": entity, "tag": TAG_BY_ENTITY.get(entity), "group": _catalog_group(entity)}
            for entity in sorted(SPECS_BY_ENTITY)
        ]
    }


@router.get("/guardrails")
async def list_guardrails(request: Request) -> dict[str, Any]:
    return {"guardrails": await asyncio.to_thread(_service(request).list)}


@router.get("/guardrails/{bundle_id}")
async def guardrail(bundle_id: str, request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(_service(request).summary, bundle_id)


@router.post("/guardrails/{bundle_id}/draft", status_code=201)
async def create_draft(bundle_id: str, body: DraftCreate, request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(_service(request).create_draft, bundle_id, body.base_version)


@router.get("/guardrails/{bundle_id}/draft")
async def get_draft(bundle_id: str, request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(_service(request).get_draft, bundle_id)


@router.put("/guardrails/{bundle_id}/draft")
async def save_draft(bundle_id: str, body: DraftSave, request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(_service(request).save_draft, bundle_id, body.bundle)


@router.delete("/guardrails/{bundle_id}/draft", status_code=204)
async def discard_draft(bundle_id: str, request: Request) -> None:
    await asyncio.to_thread(_service(request).discard_draft, bundle_id)


@router.post("/guardrails/{bundle_id}/draft/test")
async def test_draft(bundle_id: str, request: Request) -> dict[str, Any]:
    return await _service(request).test_draft(bundle_id)


@router.post("/guardrails/{bundle_id}/draft/publish")
async def publish(bundle_id: str, body: PublishRequest, request: Request) -> dict[str, Any]:
    return await _service(request).publish(
        bundle_id, activate=body.activate, accept=body.accept, note=body.note
    )


@router.post("/guardrails/{bundle_id}/activate")
async def activate(bundle_id: str, body: ActivateRequest, request: Request) -> dict[str, Any]:
    return await asyncio.to_thread(_service(request).activate, bundle_id, body.version, body.note)


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
        app.state.settings = cfg
        app.state.service = PolicyService(
            GuardrailRepository(cfg.policy_dir),
            DraftStore(cfg.control_db_path),
            engine,
            signing_key,
        )
        try:
            yield
        finally:
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
