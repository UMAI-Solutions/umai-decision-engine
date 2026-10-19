"""HTTP routes (docs/contracts/evaluation-api.md)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from umai_decision_engine.contracts.credentials import Scope
from umai_decision_engine.contracts.evaluation import (
    BundleInfo,
    DetectedEntityView,
    DetectResponse,
    EvaluationRequest,
    EvaluationResponse,
    InputTooLargeDetail,
    input_size,
)
from umai_decision_engine.l0.target import extract_target_text
from umai_decision_engine.policy.validate import BundleValidationError
from umai_decision_engine.tenancy.admission import Principal, require_scope

router = APIRouter()
log = logging.getLogger("umai.de.api")

# Admission (ADR-0023): credential → principal (tenant, scopes), rate limit, scope per route.
_EVALUATE = Depends(require_scope(Scope.EVALUATE))
_BUNDLES_READ = Depends(require_scope(Scope.BUNDLES_READ))
_BUNDLES_RELOAD = [Depends(require_scope(Scope.BUNDLES_RELOAD))]

TENANT_HEADER = "X-Umai-Tenant"


def _bundle_tenant(request: Request, principal: Principal) -> str:
    """Whose bundles a read shows: the key's tenant; for the shared operator token the
    ``X-Umai-Tenant`` header, else the deployment's default tenant (evaluation API 0.7)."""

    if principal.tenant_id is not None:
        header = request.headers.get(TENANT_HEADER)
        if header is not None and header != principal.tenant_id:
            raise HTTPException(
                403,
                {"error": "tenant_mismatch", "detail": "the credential belongs to another tenant"},
            )
        return principal.tenant_id
    return request.headers.get(TENANT_HEADER) or request.app.state.store.default_tenant


def _enforce_input_size(request: Request, body: EvaluationRequest) -> None:
    """Prompt-size admission (ADR-0019 §4, evaluation API 0.9): over the limit nothing is
    evaluated and nothing is truncated; the caller's failure policy decides what happens next."""

    limit = request.app.state.settings.max_input_chars
    size = input_size(body.content)
    if size > limit:
        log.info(
            "admission.refused reason=input_too_large request_id=%s tenant=%s size=%d limit=%d",
            body.request_id,
            body.tenant_id,
            size,
            limit,
        )
        detail = InputTooLargeDetail(
            detail=f"the content has {size} characters; this deployment accepts at most {limit}",
            limit=limit,
            size=size,
        )
        raise HTTPException(status_code=413, detail=detail.model_dump(mode="json"))


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    state = request.app.state
    if not getattr(state, "ready", False):
        return JSONResponse({"status": "starting"}, status_code=503)
    store_state = state.store.state
    return JSONResponse(
        {
            "status": "ready",
            "bundles": len(store_state.bundles),
            "load_errors": len(store_state.errors),
        }
    )


@router.post(
    "/v1/evaluate",
    response_model=EvaluationResponse,
    response_model_exclude_none=True,
)
async def evaluate(
    body: EvaluationRequest, request: Request, principal: Principal = _EVALUATE
) -> EvaluationResponse:
    _require_ready(request)
    body = body.model_copy(update={"tenant_id": principal.bind_tenant(body.tenant_id)})
    _enforce_input_size(request, body)
    try:
        return await request.app.state.evaluator.evaluate(body)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/v1/detect", response_model=DetectResponse)
async def detect(
    body: EvaluationRequest, request: Request, principal: Principal = _EVALUATE
) -> DetectResponse:
    """Development endpoint: L0 only. Disabled unless ``expose_detect_endpoint``."""

    state = request.app.state
    if not state.settings.expose_detect_endpoint:
        raise HTTPException(status_code=404)
    _require_ready(request)
    body = body.model_copy(update={"tenant_id": principal.bind_tenant(body.tenant_id)})
    _enforce_input_size(request, body)
    try:
        loaded, missing = state.evaluator.resolve_bundle(body)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BundleValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if loaded is None:
        raise HTTPException(
            status_code=404, detail=missing.message if missing else "bundle not found"
        )
    bundle = loaded.bundle
    text = extract_target_text(body.content, bundle.l0.target)
    result = await state.detector.adetect(
        text, tenant_id=body.tenant_id, config=bundle.l0, language_hint=body.content.language
    )
    return DetectResponse(
        request_id=body.request_id,
        bundle=BundleInfo(
            id=bundle.id, version=bundle.version, mode=bundle.mode, sha256=loaded.sha256
        ),
        language=result.language,
        entities=[
            DetectedEntityView(
                type=e.type,
                start=e.start,
                end=e.end,
                score=e.score,
                validated=e.validated,
                placeholder=e.placeholder,
            )
            for e in result.entities
        ],
        summary=result.summary,
        redacted_text=result.redacted_text,
        truncated=result.truncated,
        latency_ms=result.latency_ms,
    )


@router.get("/v1/bundles")
async def bundles(request: Request, principal: Principal = _BUNDLES_READ) -> dict[str, Any]:
    """One tenant's loaded and active bundle versions (ADR-0019 §2). Load errors name
    locations, not content, and are shown only to the operator token."""

    store = request.app.state.store
    state = store.state
    tenant = _bundle_tenant(request, principal)
    by_id: dict[str, dict[str, Any]] = {}
    for (owner, bundle_id, version), loaded in sorted(state.bundles.items()):
        if owner != tenant:
            continue
        entry = by_id.setdefault(
            bundle_id,
            {
                "id": bundle_id,
                "active_version": state.active.get((tenant, bundle_id)),
                "versions": [],
            },
        )
        entry["versions"].append(
            {
                "version": version,
                "sha256": loaded.sha256,
                "signed": loaded.signed,
                "key_id": loaded.key_id,
                "mode": loaded.bundle.mode,
                "name": loaded.bundle.name,
                "loaded_at": loaded.loaded_at.isoformat(),
            }
        )
    errors = (
        [{"location": e.location, "error": e.error, "at": e.at.isoformat()} for e in state.errors]
        if principal.tenant_id is None
        else []
    )
    return {
        "source": store.source_name,
        "tenant_id": tenant,
        "reloaded_at": state.reloaded_at.isoformat() if state.reloaded_at else None,
        "bundles": list(by_id.values()),
        "errors": errors,
    }


@router.get("/v1/bundles/{bundle_id}/versions/{version}")
async def bundle_version(
    bundle_id: str, version: int, request: Request, principal: Principal = _BUNDLES_READ
) -> dict[str, Any]:
    """One loaded bundle (guardrail) version of the caller's tenant with its content, for the
    console's guardrail view. Bundles never contain secrets (ADR-0012); content is policy data
    only. Another tenant's bundle is reported as not loaded (404), never shown."""

    store = request.app.state.store
    tenant = _bundle_tenant(request, principal)
    loaded = store.get(bundle_id, version, tenant_id=tenant)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"bundle {bundle_id} v{version} is not loaded")
    return {
        "id": bundle_id,
        "version": version,
        "tenant_id": tenant,
        "active": store.state.active.get((tenant, bundle_id)) == version,
        "sha256": loaded.sha256,
        "signed": loaded.signed,
        "key_id": loaded.key_id,
        "loaded_at": loaded.loaded_at.isoformat(),
        "bundle": loaded.bundle.model_dump(mode="json", by_alias=True, exclude_none=True),
    }


@router.post("/v1/bundles/reload", dependencies=_BUNDLES_RELOAD)
async def reload_bundles(request: Request) -> dict[str, Any]:
    """Rescan the bundle source now (the "new version available" signal; ADR-0013)."""

    changed = await asyncio.to_thread(request.app.state.store.reload)
    return {"changed": changed}


def _require_ready(request: Request) -> None:
    if not getattr(request.app.state, "ready", False):
        raise HTTPException(status_code=503, detail="not ready")
