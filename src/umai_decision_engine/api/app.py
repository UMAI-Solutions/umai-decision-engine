"""FastAPI application factory.

uvicorn umai_decision_engine.api.app:app --port 9100
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from umai_decision_engine.api import openapi
from umai_decision_engine.api.errors import validation_error_handler
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.l1.decision_models.registry import DecisionModels
from umai_decision_engine.policy.store import BundleStore
from umai_decision_engine.runtime import start_runtime
from umai_decision_engine.settings import Settings, get_settings

# The version of docs/contracts/evaluation-api.md this app implements. The OpenAPI document says
# so, and tests/unit/test_api_docs.py holds the two together (ADR-0054).
API_VERSION = "0.14"

DESCRIPTION = """Check content against your organization's guardrail before it goes to a model,
after the model answers, and around an agent's tool calls. One call per check; the answer is the
decision to act on.

Guides, limits and the error catalogue: the API documentation at `/docs/` on your UMAI address."""


def create_app(
    settings: Settings | None = None,
    *,
    store: BundleStore | None = None,
    detector: EntityDetector | None = None,
    decision_models: DecisionModels | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = app.state
        state.ready = False
        runtime = await start_runtime(
            settings or get_settings(),
            store=store,
            detector=detector,
            decision_models=decision_models,
        )
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

    app = FastAPI(
        title="UMAI evaluation API",
        version=API_VERSION,
        description=DESCRIPTION,
        openapi_tags=[{"name": "Evaluation", "description": "Decide whether content may go on."}],
        lifespan=lifespan,
    )
    openapi.install(app)
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # gap L1
    from umai_decision_engine.api.routes import router

    app.include_router(router)
    return app


app = create_app()
