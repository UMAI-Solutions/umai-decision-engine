"""Counting requests in the usage ledger for plans and quotas (ADR-0043): the month a quota
covers, what counts as a request, and the count per environment. One definition for the
engine's quota counter and the policy service's ``GET /v1/entitlements``."""

from __future__ import annotations

import time
import uuid
from datetime import datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from umai_decision_engine.db.models import Tenant, UsageEventRow
from umai_decision_engine.db.session import global_session, tenant_session
from umai_decision_engine.tenancy.quota import month_bounds

__all__ = ["LedgerCounts", "count_requests", "is_request", "month_bounds"]

_UNKNOWN_TTL_S = 60.0  # how long a tenant the store does not know is not looked up again


def is_request(row=UsageEventRow):
    """A request is an admitted evaluation API call or an admitted AI Gateway call (ADR-0043
    D2): the gateway's own checks (``evaluations`` with source ``gateway``) do not add to it."""

    return or_(and_(row.meter == "evaluations", row.source == "api"), row.meter == "gateway_calls")


async def count_requests(
    session: AsyncSession, tenant: uuid.UUID, start: datetime, end: datetime
) -> dict[str, int]:
    """Requests in ``[start, end)`` per quota environment: ``test`` for test keys, ``live`` for
    everything else (live keys, the operator token, the console; D6)."""

    u = UsageEventRow
    rows = (
        await session.execute(
            select(u.environment, func.sum(u.quantity))
            .where(
                u.tenant_id == tenant, u.occurred_at >= start, u.occurred_at < end, is_request(u)
            )
            .group_by(u.environment)
        )
    ).all()
    counts = {"live": 0, "test": 0}
    for environment, quantity in rows:
        counts["test" if environment == "test" else "live"] += int(quantity or 0)
    return counts


class LedgerCounts:
    """The engine side: a tenant's month count by its external id. Tenant ids are resolved once
    per process; a tenant the store does not know counts as zero."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._tenants: dict[str, uuid.UUID] = {}
        self._unknown: dict[str, float] = {}

    async def _tenant(self, slug: str) -> uuid.UUID | None:
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

    async def month(self, slug: str, start: datetime, end: datetime) -> dict[str, int]:
        tenant = await self._tenant(slug)
        if tenant is None:
            return {"live": 0, "test": 0}
        async with tenant_session(self._engine, tenant) as session:
            return await count_requests(session, tenant, start, end)
