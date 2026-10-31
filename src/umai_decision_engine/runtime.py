"""What the engine API and the gateway share at run time: bundle store, L0 detector, evaluator,
key registry and admission, with their background reloads. Both apps build it in their lifespan.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from umai_decision_engine.applications import AllowList, ApplicationTable
from umai_decision_engine.events.recorder import NullRecorder, QueueRecorder, Recorder
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.l1.decision_models.registry import DecisionModels
from umai_decision_engine.pipeline.orchestrator import Evaluator
from umai_decision_engine.policy.sources import DirectorySource
from umai_decision_engine.policy.store import BundleStore
from umai_decision_engine.settings import Settings
from umai_decision_engine.telemetry.logging import configure_logging
from umai_decision_engine.tenancy.admission import Admission
from umai_decision_engine.tenancy.limits import FailedAuthThrottle, RateLimiter
from umai_decision_engine.tenancy.network import parse_networks
from umai_decision_engine.tenancy.quota import QuotaCounter
from umai_decision_engine.tenancy.registry import DirectoryKeyRegistrySource, KeyRegistry

log = logging.getLogger("umai.runtime")


@dataclass
class Runtime:
    settings: Settings
    store: BundleStore
    detector: EntityDetector
    evaluator: Evaluator
    registry: KeyRegistry | None
    admission: Admission
    applications: ApplicationTable
    owns_detector: bool
    recorder: Recorder = field(default_factory=NullRecorder)
    tasks: list[asyncio.Task] = field(default_factory=list)

    def poll(self, coroutine) -> None:
        """Run a background reload loop until :meth:`stop`."""

        self.tasks.append(asyncio.create_task(coroutine))

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.tasks.clear()
        await self.evaluator.l1b.drain()  # shadow outcomes are records: before the flush
        await self.evaluator.l1b.aclose()
        if isinstance(self.recorder, QueueRecorder):
            await self.recorder.aclose(self.settings.records_drain_timeout_ms / 1000)
            await self.recorder.writer_aclose()
        if self.owns_detector:
            self.detector.close()


def records_database(cfg: Settings):
    """The tenant store a process writes records to and reads its quota counts from, or
    ``None`` (ADR-0041, ADR-0043): explicit, never implied."""

    if cfg.records_sink == "none":
        return None
    if cfg.database_url is None:
        raise RuntimeError("UMAI_DE_RECORDS_SINK=database requires UMAI_DE_DATABASE_URL")
    # Imported here: only a process that writes records loads the database layer.
    from umai_decision_engine.db.session import make_engine

    return make_engine(cfg.database_url.get_secret_value())


def records_recorder(cfg: Settings, database=None) -> Recorder:
    """The recorder the settings ask for (ADR-0041, ADR-0042): explicit, never implied."""

    database = database or records_database(cfg)
    if database is None:
        return NullRecorder()
    from umai_decision_engine.events.database import DatabaseRecordWriter

    return QueueRecorder(
        DatabaseRecordWriter(database),
        queue_size=cfg.records_queue_size,
        batch_size=cfg.records_batch_size,
        flush_interval_s=cfg.records_flush_interval_ms / 1000,
        write_timeout_s=cfg.records_write_timeout_ms / 1000,
        stats_interval_s=cfg.records_stats_interval_s,
    )


async def start_runtime(
    cfg: Settings,
    *,
    store: BundleStore | None = None,
    detector: EntityDetector | None = None,
    decision_models: DecisionModels | None = None,
    recorder: Recorder | None = None,
) -> Runtime:
    """``recorder`` overrides the one the settings ask for (tests, the leak suite)."""

    if cfg.env != "development" and cfg.api_token is None and cfg.key_registry_dir is None:
        raise RuntimeError(
            "UMAI_DE_API_TOKEN or UMAI_DE_KEY_REGISTRY_DIR is required outside development"
            " (ADR-0016, ADR-0023)"
        )
    configure_logging(cfg.log_level)
    store = store or BundleStore(
        DirectorySource(cfg.policy_dir),
        public_keys=cfg.bundle_public_keys,
        require_signed=bool(cfg.require_signed_bundles),
        default_tenant=cfg.default_tenant_id,
    )
    await asyncio.to_thread(store.reload, force=True)
    owns_detector = detector is None
    detector = detector or EntityDetector(
        cfg.tenant_key, threads=cfg.l0_threads, cache_entries=cfg.l0_cache_entries
    )
    await asyncio.to_thread(detector.warm_up)
    registry = None
    if cfg.key_registry_dir is not None:
        registry = KeyRegistry(
            DirectoryKeyRegistrySource(cfg.key_registry_dir),
            public_keys=cfg.bundle_public_keys,
            require_signed=bool(cfg.require_signed_bundles),
        )
        await asyncio.to_thread(registry.reload, force=True)
    database = records_database(cfg) if recorder is None else None
    quota = None
    if registry is not None:
        # Monthly quotas (ADR-0043 D4): counted in memory from the usage ledger; a process
        # without the records store counts only what it admitted since it started.
        ledger = None
        if database is not None:
            from umai_decision_engine.events.ledger import LedgerCounts

            ledger = LedgerCounts(database)
        quota = QuotaCounter(ledger)
        limited = registry.limited_tenants()
        if limited and ledger is None:
            log.warning(
                "quota: %d tenant(s) have a monthly limit but records are off; "
                "this process counts only what it admits",
                len(limited),
            )
        quota.watch(limited)
        await quota.refresh()  # a restart does not forget a used quota
    admission = Admission(
        shared_token=cfg.api_token.get_secret_value() if cfg.api_token else None,
        registry=registry,
        limiter=RateLimiter(cfg.rate_limit_rps, cfg.rate_limit_burst)
        if cfg.rate_limit_rps
        else None,
        development=cfg.env == "development",
        quota=quota,
        trusted_proxies=parse_networks(cfg.trusted_proxies),
        throttle=FailedAuthThrottle(
            cfg.auth_failure_limit,
            cfg.auth_failure_window_s,
            max_sources=cfg.auth_failure_max_sources,
        )
        if cfg.auth_failure_limit
        else None,
    )
    log.info(
        "admission: trusted_proxies=%d failed_credentials=%s",
        len(admission.trusted_proxies),
        f"{cfg.auth_failure_limit}/{cfg.auth_failure_window_s:g}s"
        if cfg.auth_failure_limit
        else "off",
    )
    applications = ApplicationTable(cfg.applications_dir, AllowList(cfg.gateway_upstream_allowlist))
    await asyncio.to_thread(applications.reload, force=True)
    decision_models = decision_models or DecisionModels.from_settings(cfg)
    if cfg.decision_warm_up_timeout_ms:
        # Last before taking traffic: the first L1b calls reuse these connections.
        await decision_models.warm_up(cfg.decision_warm_up_timeout_ms / 1000)
    recorder = recorder or records_recorder(cfg, database)
    runtime = Runtime(
        settings=cfg,
        store=store,
        detector=detector,
        evaluator=Evaluator(
            store,
            detector,
            cfg,
            decision_models=decision_models,
            recorder=recorder,
        ),
        registry=registry,
        admission=admission,
        applications=applications,
        owns_detector=owns_detector,
        recorder=recorder,
    )
    if isinstance(recorder, QueueRecorder):
        recorder.start()
    if cfg.bundle_poll_interval_s > 0:
        runtime.poll(store.poll(cfg.bundle_poll_interval_s))
    if registry is not None and cfg.key_registry_poll_interval_s > 0:
        runtime.poll(registry.poll(cfg.key_registry_poll_interval_s))
    if quota is not None:
        runtime.poll(quota.run(cfg.quota_refresh_interval_s))
    if cfg.applications_poll_interval_s > 0:
        runtime.poll(applications.poll(cfg.applications_poll_interval_s))
    return runtime
