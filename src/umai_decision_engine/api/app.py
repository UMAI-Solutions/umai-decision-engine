"""FastAPI application factory.

uvicorn umai_decision_engine.api.app:app --port 9100
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from umai_decision_engine import __version__
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.pipeline.orchestrator import Evaluator
from umai_decision_engine.policy.sources import DirectorySource
from umai_decision_engine.policy.store import BundleStore
from umai_decision_engine.settings import Settings, get_settings
from umai_decision_engine.telemetry.logging import configure_logging
from umai_decision_engine.tenancy.admission import Admission
from umai_decision_engine.tenancy.limits import RateLimiter
from umai_decision_engine.tenancy.registry import DirectoryKeyRegistrySource, KeyRegistry


def create_app(
    settings: Settings | None = None,
    *,
    store: BundleStore | None = None,
    detector: EntityDetector | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg = settings or get_settings()
        if cfg.env != "development" and cfg.api_token is None and cfg.key_registry_dir is None:
            raise RuntimeError(
                "UMAI_DE_API_TOKEN or UMAI_DE_KEY_REGISTRY_DIR is required outside development"
                " (ADR-0016, ADR-0023)"
            )
        configure_logging(cfg.log_level)
        state = app.state
        state.ready = False
        state.settings = cfg
        state.store = store or BundleStore(
            DirectorySource(cfg.policy_dir),
            public_keys=cfg.bundle_public_keys,
            require_signed=bool(cfg.require_signed_bundles),
            default_tenant=cfg.default_tenant_id,
        )
        await asyncio.to_thread(state.store.reload, force=True)
        state.detector = detector or EntityDetector(cfg.tenant_key, threads=cfg.l0_threads)
        await asyncio.to_thread(state.detector.warm_up)
        state.evaluator = Evaluator(state.store, state.detector, cfg)
        registry = None
        if cfg.key_registry_dir is not None:
            registry = KeyRegistry(
                DirectoryKeyRegistrySource(cfg.key_registry_dir),
                public_keys=cfg.bundle_public_keys,
                require_signed=bool(cfg.require_signed_bundles),
            )
            await asyncio.to_thread(registry.reload, force=True)
        state.registry = registry
        state.admission = Admission(
            shared_token=cfg.api_token.get_secret_value() if cfg.api_token else None,
            registry=registry,
            limiter=RateLimiter(cfg.rate_limit_rps, cfg.rate_limit_burst)
            if cfg.rate_limit_rps
            else None,
            development=cfg.env == "development",
        )
        pollers = []
        if cfg.bundle_poll_interval_s > 0:
            pollers.append(asyncio.create_task(state.store.poll(cfg.bundle_poll_interval_s)))
        if registry is not None and cfg.key_registry_poll_interval_s > 0:
            pollers.append(asyncio.create_task(registry.poll(cfg.key_registry_poll_interval_s)))
        state.ready = True
        try:
            yield
        finally:
            state.ready = False
            for poller in pollers:
                poller.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poller
            if detector is None:
                state.detector.close()

    app = FastAPI(title="umai-decision-engine", version=__version__, lifespan=lifespan)
    from umai_decision_engine.api.routes import router

    app.include_router(router)
    return app


app = create_app()
