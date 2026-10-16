"""HTTP routes (docs/contracts/evaluation-api.md)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from umai_decision_engine.contracts.evaluation import (
    BundleInfo,
    DetectedEntityView,
    DetectResponse,
    EvaluationRequest,
    EvaluationResponse,
)
from umai_decision_engine.l0.target import extract_target_text
from umai_decision_engine.policy.validate import BundleValidationError

router = APIRouter()


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


@router.post("/v1/evaluate", response_model=EvaluationResponse, response_model_exclude_none=True)
async def evaluate(body: EvaluationRequest, request: Request) -> EvaluationResponse:
    _require_ready(request)
    try:
        return await request.app.state.evaluator.evaluate(body)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/v1/detect", response_model=DetectResponse)
async def detect(body: EvaluationRequest, request: Request) -> DetectResponse:
    """Development endpoint: L0 only. Disabled unless ``expose_detect_endpoint``."""

    state = request.app.state
    if not state.settings.expose_detect_endpoint:
        raise HTTPException(status_code=404)
    _require_ready(request)
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
async def bundles(request: Request) -> dict[str, Any]:
    """Loaded and active bundle versions and load errors (for operators and the dashboard)."""

    store = request.app.state.store
    state = store.state
    by_id: dict[str, dict[str, Any]] = {}
    for (bundle_id, version), loaded in sorted(state.bundles.items()):
        entry = by_id.setdefault(
            bundle_id,
            {"id": bundle_id, "active_version": state.active.get(bundle_id), "versions": []},
        )
        entry["versions"].append(
            {
                "version": version,
                "sha256": loaded.sha256,
                "signed": loaded.signed,
                "key_id": loaded.key_id,
                "mode": loaded.bundle.mode,
                "loaded_at": loaded.loaded_at.isoformat(),
            }
        )
    return {
        "source": store.source_name,
        "reloaded_at": state.reloaded_at.isoformat() if state.reloaded_at else None,
        "bundles": list(by_id.values()),
        "errors": [
            {"location": e.location, "error": e.error, "at": e.at.isoformat()} for e in state.errors
        ],
    }


@router.post("/v1/bundles/reload")
async def reload_bundles(request: Request) -> dict[str, Any]:
    """Rescan the bundle source now (the "new version available" signal; ADR-0013)."""

    changed = await asyncio.to_thread(request.app.state.store.reload)
    return {"changed": changed}


def _require_ready(request: Request) -> None:
    if not getattr(request.app.state, "ready", False):
        raise HTTPException(status_code=503, detail="not ready")
