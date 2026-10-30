"""The records writer for the tenant store (ADR-0041, ADR-0042): one ``tenant_session`` per
tenant and batch, inserts that ignore rows already written (the tables' uniqueness makes a
retried batch idempotent). Application role: INSERT and SELECT only."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncEngine

from umai_decision_engine.contracts.events import (
    DecisionEvent,
    L1bOutcomeRecord,
    Record,
    UsageRecord,
)
from umai_decision_engine.db.models import (
    DecisionEventRow,
    L1bOutcomeRow,
    Tenant,
    UsageEventRow,
)
from umai_decision_engine.db.session import global_session, is_postgres, tenant_session
from umai_decision_engine.events.recorder import WriteResult

_UNKNOWN_TTL_S = 60.0  # how long a tenant the store does not know is not looked up again


def _json(model) -> dict | list | None:
    return (
        None if model is None else model.model_dump(mode="json", by_alias=True, exclude_none=True)
    )


def _event_row(event: DecisionEvent, tenant: uuid.UUID) -> dict:
    decision = event.decision
    return {
        "id": uuid.uuid4(),
        "tenant_id": tenant,
        "event_id": event.event_id,
        "occurred_at": event.occurred_at,
        "source": event.source,
        "request_id": event.request_id,
        "engine_version": event.engine_version,
        "key_id": event.key_id,
        "environment": event.environment,
        "application_id": event.application_id,
        "gateway_call_id": event.gateway_call_id,
        "bundle_id": event.bundle.id,
        "bundle_version": event.bundle.version,
        "bundle_sha256": event.bundle.sha256,
        "bundle_mode": event.bundle.mode,
        "phase": event.phase,
        "action": decision.action,
        "severity": decision.severity,
        "decided_by": decision.decided_by,
        "degraded": decision.degraded,
        "floor_action": decision.floor,
        "would_have": decision.would_have,
        "review": event.review,
        "review_reasons": list(event.review_reasons),
        "subject": _json(event.subject),
        "destination": _json(event.destination),
        "agent_action": _json(event.agent_action),
        "entities": [_json(e) for e in event.entities],
        "tags": [_json(t) for t in event.tags],
        "matched": [_json(m) for m in event.matched],
        "l1b": _json(event.l1b),
        "errors": [_json(e) for e in event.errors],
        "latency_total_ms": event.latency_ms.total,
        "latency_l0_ms": event.latency_ms.l0,
        "latency_l1b_ms": event.latency_ms.l1b,
    }


def _outcome_row(record: L1bOutcomeRecord, tenant: uuid.UUID) -> dict:
    values = record.model_dump(exclude={"contract_version", "tenant_id"})
    return {**values, "id": uuid.uuid4(), "tenant_id": tenant}


def _usage_row(record: UsageRecord, tenant: uuid.UUID) -> dict:
    values = record.model_dump(exclude={"contract_version", "tenant_id"})
    return {**values, "id": uuid.uuid4(), "tenant_id": tenant}


_ROWS = {
    DecisionEvent: (DecisionEventRow, _event_row),
    L1bOutcomeRecord: (L1bOutcomeRow, _outcome_row),
    UsageRecord: (UsageEventRow, _usage_row),
}


class DatabaseRecordWriter:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        insert = postgresql.insert if is_postgres(engine) else sqlite.insert
        # One statement per table, compiled once and executed for many rows (executemany):
        # a fresh multi-VALUES statement per batch costs milliseconds of event-loop time.
        self._statements = {
            model: insert(model.__table__).on_conflict_do_nothing() for model, _ in _ROWS.values()
        }
        self._tenants: dict[str, uuid.UUID] = {}
        self._unknown: dict[str, float] = {}

    async def _tenant(self, slug: str) -> uuid.UUID | None:
        """The tenant's uuid (what RLS compares) for its external id, cached per process."""

        if slug in self._tenants:
            return self._tenants[slug]
        seen = self._unknown.get(slug)
        if seen is not None and time.monotonic() - seen < _UNKNOWN_TTL_S:
            return None
        async with global_session(self._engine) as session:
            tenant = await session.scalar(select(Tenant.id).where(Tenant.slug == slug))
        if tenant is None:
            self._unknown[slug] = time.monotonic()
            return None
        self._tenants[slug] = tenant
        return tenant

    async def write(self, batch: list[Record]) -> WriteResult:
        by_tenant: dict[str, list[Record]] = defaultdict(list)
        for record in batch:
            by_tenant[record.tenant_id].append(record)
        result = WriteResult()
        for slug, records in by_tenant.items():
            tenant = await self._tenant(slug)
            if tenant is None:
                result.unknown_tenant += len(records)
                continue
            rows: dict[type, list[dict]] = defaultdict(list)
            for record in records:
                model, row = _ROWS[type(record)]
                rows[model].append(row(record, tenant))
            async with tenant_session(self._engine, tenant) as session:
                for model, values in rows.items():
                    await session.execute(self._statements[model], values)
            result.written += len(records)
        return result

    async def aclose(self) -> None:
        await self._engine.dispose()
