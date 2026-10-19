"""UMAI secure gateway: OpenAI-compatible surface with guardrails in and out (ADR-0034).

uvicorn umai_decision_engine.gateway.app:app --port 9300
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from umai_decision_engine import __version__
from umai_decision_engine.gateway.chat import router
from umai_decision_engine.gateway.errors import GatewayHTTPError, gateway_error_handler
from umai_decision_engine.gateway.guard import Guard
from umai_decision_engine.gateway.responses import router as responses_router
from umai_decision_engine.gateway.routing import AllowList, RouteTable
from umai_decision_engine.gateway.upstream import UpstreamClient
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.policy.store import BundleStore
from umai_decision_engine.runtime import start_runtime
from umai_decision_engine.settings import Settings, get_settings


def create_gateway_app(
    settings: Settings | None = None,
    *,
    store: BundleStore | None = None,
    detector: EntityDetector | None = None,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = app.state
        state.ready = False
        runtime = await start_runtime(settings or get_settings(), store=store, detector=detector)
        cfg = runtime.settings
        routes = RouteTable(cfg.gateway_routes_dir, AllowList(cfg.gateway_upstream_allowlist))
        await asyncio.to_thread(routes.reload, force=True)
        if cfg.gateway_routes_poll_interval_s > 0:
            runtime.poll(routes.poll(cfg.gateway_routes_poll_interval_s))
        state.settings = cfg
        state.store = runtime.store
        state.admission = runtime.admission
        state.routes = routes
        state.guard = Guard(runtime.evaluator, runtime.store)
        state.upstream = UpstreamClient(upstream_transport)
        state.ready = True
        try:
            yield
        finally:
            state.ready = False
            await state.upstream.aclose()
            await runtime.stop()

    app = FastAPI(title="umai-gateway", version=__version__, lifespan=lifespan)
    app.add_exception_handler(GatewayHTTPError, gateway_error_handler)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request) -> JSONResponse:
        ready = bool(getattr(request.app.state, "ready", False))
        return JSONResponse({"ready": ready}, status_code=200 if ready else 503)

    app.include_router(router)
    app.include_router(responses_router)
    return app


app = create_gateway_app()
