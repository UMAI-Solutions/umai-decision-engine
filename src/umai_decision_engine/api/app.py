"""FastAPI application factory.

uvicorn umai_decision_engine.api.app:app --port 9100
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from umai_decision_engine import __version__
from umai_decision_engine.api.errors import validation_error_handler
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.policy.store import BundleStore
from umai_decision_engine.runtime import start_runtime
from umai_decision_engine.settings import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    *,
    store: BundleStore | None = None,
    detector: EntityDetector | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = app.state
        state.ready = False
        runtime = await start_runtime(settings or get_settings(), store=store, detector=detector)
        state.settings = runtime.settings
        state.store = runtime.store
        state.detector = runtime.detector
        state.evaluator = runtime.evaluator
        state.registry = runtime.registry
        state.admission = runtime.admission
        state.applications = runtime.applications
        state.ready = True
        try:
            yield
        finally:
            state.ready = False
            await runtime.stop()

    app = FastAPI(title="umai-decision-engine", version=__version__, lifespan=lifespan)
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # gap L1
    from umai_decision_engine.api.routes import router

    app.include_router(router)
    return app


app = create_app()
