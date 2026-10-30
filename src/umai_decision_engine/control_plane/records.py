"""Records in the policy service (ADR-0041 §5, §7): the read API over decision events, L1b outcomes
and the usage ledger, and the retention job. Reads run in the caller's tenant session (RLS);
retention runs per tenant with the owner role, the only role allowed to delete records."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine

from umai_decision_engine.contracts.events import (
    DecisionEventDetail,
    DecisionEventPage,
    UsageGroup,
    UsageSummary,
)
from umai_decision_engine.db.models import (
    DecisionEventRow,
    L1bOutcomeRow,
    Tenant,
    UsageEventRow,
)
from umai_decision_engine.db.repositories import TenantInfo
from umai_decision_engine.db.session import global_session, tenant_session
from umai_decision_engine.events.database import event_from_row, outcome_from_row

log = logging.getLogger("umai.control.records")


class BadCursor(ValueError):
    pass


def _encode_cursor(row: DecisionEventRow) -> str:
    raw = f"{row.occurred_at.isoformat()}|{row.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, row_id = raw.split("|", 1)
        return datetime.fromisoformat(at), uuid.UUID(row_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise BadCursor("not a cursor from a previous page") from exc


def month_bounds(month: str | None, now: datetime | None = None) -> tuple[str, datetime, datetime]:
    """``YYYY-MM`` (default: the current month, UTC) → its first instant and the next month's."""

    now = now or datetime.now(UTC)
    if month is None:
        month = f"{now:%Y-%m}"
    start = datetime.strptime(month + "-01", "%Y-%m-%d").replace(tzinfo=UTC)
    end = (start + timedelta(days=32)).replace(day=1)
    return month, start, end


class RecordsReader:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def events(
        self,
        tenant: TenantInfo,
        *,
        limit: int = 50,
        cursor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        action: str | None = None,
        source: str | None = None,
        application_id: str | None = None,
        phase: str | None = None,
        review: bool | None = None,
    ) -> DecisionEventPage:
        row = DecisionEventRow
        query = select(row).where(row.tenant_id == tenant.id)
        for column, value in (
            (row.action, action),
            (row.source, source),
            (row.application_id, application_id),
            (row.phase, phase),
            (row.review, review),
        ):
            if value is not None:
                query = query.where(column == value)
        if since is not None:
            query = query.where(row.occurred_at >= since)
        if until is not None:
            query = query.where(row.occurred_at < until)
        if cursor is not None:
            at, row_id = _decode_cursor(cursor)
            query = query.where(
                or_(row.occurred_at < at, and_(row.occurred_at == at, row.id < row_id))
            )
        query = query.order_by(row.occurred_at.desc(), row.id.desc()).limit(limit + 1)
        async with tenant_session(self._engine, tenant.id) as session:
            rows = list((await session.scalars(query)).all())
        page, more = rows[:limit], len(rows) > limit
        return DecisionEventPage(
            events=[event_from_row(r, tenant.slug) for r in page],
            next_cursor=_encode_cursor(page[-1]) if more and page else None,
        )

    async def event(self, tenant: TenantInfo, event_id: str) -> DecisionEventDetail | None:
        async with tenant_session(self._engine, tenant.id) as session:
            row = await session.scalar(
                select(DecisionEventRow).where(
                    DecisionEventRow.tenant_id == tenant.id, DecisionEventRow.event_id == event_id
                )
            )
            if row is None:
                return None
            outcomes = (
                await session.scalars(
                    select(L1bOutcomeRow)
                    .where(L1bOutcomeRow.tenant_id == tenant.id, L1bOutcomeRow.event_id == event_id)
                    .order_by(L1bOutcomeRow.policy_id)
                )
            ).all()
        return DecisionEventDetail(
            event=event_from_row(row, tenant.slug),
            l1b_outcomes=[outcome_from_row(o, tenant.slug) for o in outcomes],
        )

    async def usage(self, tenant: TenantInfo, month: str | None = None) -> UsageSummary:
        month, start, end = month_bounds(month)
        u = UsageEventRow
        query = (
            select(u.meter, u.source, u.environment, u.application_id, func.sum(u.quantity))
            .where(u.tenant_id == tenant.id, u.occurred_at >= start, u.occurred_at < end)
            .group_by(u.meter, u.source, u.environment, u.application_id)
            .order_by(u.meter, u.source, u.environment, u.application_id)
        )
        async with tenant_session(self._engine, tenant.id) as session:
            rows = (await session.execute(query)).all()
        groups = [
            UsageGroup(
                meter=meter,
                source=source,
                environment=environment,
                application_id=application_id,
                quantity=int(quantity or 0),
            )
            for meter, source, environment, application_id, quantity in rows
        ]
        totals: dict[str, int] = {}
        for group in groups:
            totals[group.meter] = totals.get(group.meter, 0) + group.quantity
        return UsageSummary.model_validate(
            {
                "tenant_id": tenant.slug,
                "month": month,
                "from": start,
                "to": end,
                "totals": totals,
                "groups": [g.model_dump() for g in groups],
            }
        )


# ------------------------------------------------------------------ retention (ADR-0041 §5)


async def apply_retention(
    engine: AsyncEngine,
    *,
    decision_days: int,
    usage_days: int,
    now: datetime | None = None,
) -> dict[str, int]:
    """Delete records past their retention, tenant by tenant (RLS stays forced for the owner
    role too). Returns the rows deleted per table; logs counts only."""

    now = now or datetime.now(UTC)
    cutoffs = {
        DecisionEventRow: now - timedelta(days=decision_days),
        L1bOutcomeRow: now - timedelta(days=decision_days),
        UsageEventRow: now - timedelta(days=usage_days),
    }
    async with global_session(engine) as session:
        tenants = list((await session.execute(select(Tenant.id, Tenant.slug))).all())
    deleted = {model.__tablename__: 0 for model in cutoffs}
    for tenant_id, slug in tenants:
        async with tenant_session(engine, tenant_id) as session:
            for model, cutoff in cutoffs.items():
                result = await session.execute(
                    delete(model).where(model.tenant_id == tenant_id, model.occurred_at < cutoff)
                )
                if result.rowcount:
                    deleted[model.__tablename__] += result.rowcount
                    log.info(
                        "records.retention tenant=%s table=%s deleted=%d",
                        slug,
                        model.__tablename__,
                        result.rowcount,
                    )
    return deleted


async def retention_loop(
    engine: AsyncEngine, *, decision_days: int, usage_days: int, interval_s: float
) -> None:
    """Run :func:`apply_retention` now and every ``interval_s``; a failed run is logged and
    retried at the next interval (records are kept longer, never lost early)."""

    while True:
        try:
            await apply_retention(engine, decision_days=decision_days, usage_days=usage_days)
        except Exception as exc:
            log.warning("records.retention_failed error=%s", type(exc).__name__)
        await asyncio.sleep(interval_s)
