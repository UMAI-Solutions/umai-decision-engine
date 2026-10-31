"""Records in the policy service (ADR-0041 §5, §7): the read API over decision events, L1b outcomes
and the usage ledger, and the retention job (also for the audit log, ADR-0045). Reads run in the
caller's tenant session (RLS); retention runs per tenant with the owner role, the only role
allowed to delete records."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import Row, and_, delete, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine

from umai_decision_engine.contracts.events import (
    DecisionDay,
    DecisionEvent,
    DecisionEventDetail,
    DecisionEventPage,
    DecisionEventSummary,
    EntityCount,
    L1bOutcomeRecord,
    PolicyCount,
    UsageDay,
    UsageGroup,
    UsageSummary,
)
from umai_decision_engine.contracts.exports import UsageLine
from umai_decision_engine.control_plane.webhooks import apply_webhook_retention
from umai_decision_engine.db.models import (
    AuditEventRow,
    DecisionEventRow,
    L1bOutcomeRow,
    Tenant,
    UsageEventRow,
)
from umai_decision_engine.db.repositories import TenantInfo
from umai_decision_engine.db.session import global_session, is_postgres, tenant_session
from umai_decision_engine.events.database import event_from_row, outcome_from_row
from umai_decision_engine.events.ledger import count_requests, month_bounds

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


# Events whose entity types and deciding policies the summary counts; a larger window says so
# (``truncated``) instead of answering slowly. Day and action counts are exact.
SUMMARY_SCAN = 20_000
SUMMARY_TOP = 10
SUMMARY_DEFAULT_DAYS = 30


@dataclass(frozen=True)
class EventFilters:
    """What the decision-event list, summary and CSV export select (tagged-action 0.6). ``q`` is
    an id: the event's, the caller's request id or the gateway call's, matched exactly."""

    since: datetime | None = None
    until: datetime | None = None
    action: str | None = None
    source: str | None = None
    application_id: str | None = None
    phase: str | None = None
    review: bool | None = None
    entity_type: str | None = None  # found in the event (``entities[].type``)
    policy_id: str | None = None  # took part in deciding (``matched[].id``)
    q: str | None = None


_COLUMNS = ("action", "source", "application_id", "phase", "review")
ALL_EVENTS = EventFilters()


def _events(rows: list[Row], slug: str) -> list[DecisionEvent]:
    return [event_from_row(r, slug) for r in rows]


def _outcomes(rows: list[Row], slug: str) -> list[L1bOutcomeRecord]:
    return [outcome_from_row(r, slug) for r in rows]


def _columns(model):
    return model.__table__.columns


def _after(query, model, after: tuple[datetime, uuid.UUID] | None):
    """Keyset pagination, newest first: rows older than ``after``. The redundant
    ``occurred_at <= at`` lets the ``(tenant_id, occurred_at)`` index bound the scan."""

    if after is None:
        return query
    at, row_id = after
    return query.where(
        model.occurred_at <= at,
        or_(model.occurred_at < at, and_(model.occurred_at == at, model.id < row_id)),
    )


class RecordsReader:
    def __init__(self, engine: AsyncEngine, *, summary_scan: int = SUMMARY_SCAN) -> None:
        self._engine = engine
        self._summary_scan = summary_scan

    def _day(self, column):
        """The UTC calendar day of a timestamp, as ``YYYY-MM-DD``, in the store's dialect."""

        if is_postgres(self._engine):
            return func.to_char(func.timezone("UTC", column), "YYYY-MM-DD")
        return func.strftime("%Y-%m-%d", column)

    def _has(self, column, key: str, value: str):
        """``column`` (a JSON array of objects) holds an object whose ``key`` is ``value``. No
        index: the scan's cost is measured in ADR-0047 D2, with the point to add one."""

        if is_postgres(self._engine):
            item = func.json_array_elements(column).table_valued("value").alias("item")
            match = item.c.value.op("->>")(key) == value
        else:
            item = func.json_each(column).table_valued("value").alias("item")
            match = func.json_extract(item.c.value, f"$.{key}") == value
        return exists(select(literal(1)).select_from(item).where(match))

    def _where(self, query, tenant: TenantInfo, filters: EventFilters):
        row = DecisionEventRow
        query = query.where(row.tenant_id == tenant.id)
        if filters.since is not None:
            query = query.where(row.occurred_at >= filters.since)
        if filters.until is not None:
            query = query.where(row.occurred_at < filters.until)
        for name in _COLUMNS:
            value = getattr(filters, name)
            if value is not None:
                query = query.where(getattr(row, name) == value)
        if filters.entity_type is not None:
            query = query.where(self._has(row.entities, "type", filters.entity_type))
        if filters.policy_id is not None:
            query = query.where(self._has(row.matched, "id", filters.policy_id))
        if filters.q is not None:
            query = query.where(
                or_(
                    row.event_id == filters.q,
                    row.request_id == filters.q,
                    row.gateway_call_id == filters.q,
                )
            )
        return query

    async def events(
        self,
        tenant: TenantInfo,
        filters: EventFilters = ALL_EVENTS,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> DecisionEventPage:
        rows = await self._page(
            tenant, filters, limit + 1, _decode_cursor(cursor) if cursor else None
        )
        page, more = rows[:limit], len(rows) > limit
        return DecisionEventPage(
            events=[event_from_row(r, tenant.slug) for r in page],
            next_cursor=_encode_cursor(page[-1]) if more and page else None,
        )

    async def _page(
        self,
        tenant: TenantInfo,
        filters: EventFilters,
        limit: int,
        after: tuple[datetime, uuid.UUID] | None,
    ) -> list[Row]:
        """Up to ``limit`` events, newest first, older than ``after`` (keyset pagination). Plain
        rows, not ORM objects: exports read up to 100 000 of them."""

        row = DecisionEventRow
        query = _after(self._where(select(*_columns(row)), tenant, filters), row, after)
        query = query.order_by(row.occurred_at.desc(), row.id.desc()).limit(limit)
        async with tenant_session(self._engine, tenant.id) as session:
            return list((await session.execute(query)).all())

    async def count_events(self, tenant: TenantInfo, filters: EventFilters, *, up_to: int) -> int:
        """How many events match, counted up to ``up_to`` (a bounded count for export caps)."""

        matching = self._where(select(DecisionEventRow.id), tenant, filters).limit(up_to)
        return await self._count(tenant, matching)

    async def count_rows(self, model, tenant: TenantInfo, *, up_to: int) -> int:
        """The tenant's rows of a records table, counted up to ``up_to``."""

        return await self._count(
            tenant, select(model.id).where(model.tenant_id == tenant.id).limit(up_to)
        )

    async def _count(self, tenant: TenantInfo, matching) -> int:
        async with tenant_session(self._engine, tenant.id) as session:
            return int(await session.scalar(select(func.count()).select_from(matching.subquery())))

    async def stream_outcomes(
        self, tenant: TenantInfo, *, batch: int = 1000
    ) -> AsyncIterator[list[L1bOutcomeRecord]]:
        """Every L1b outcome record of the tenant, newest first, in batches (as
        :meth:`stream_events`)."""

        o = L1bOutcomeRow
        after = None
        while True:
            query = _after(select(*_columns(o)).where(o.tenant_id == tenant.id), o, after)
            query = query.order_by(o.occurred_at.desc(), o.id.desc()).limit(batch)
            async with tenant_session(self._engine, tenant.id) as session:
                rows = list((await session.execute(query)).all())
            if rows:  # validation is CPU work: off the event loop
                yield await asyncio.to_thread(_outcomes, rows, tenant.slug)
            if len(rows) < batch:
                return
            after = (rows[-1].occurred_at, rows[-1].id)

    async def stream_events(
        self, tenant: TenantInfo, filters: EventFilters, *, batch: int = 1000
    ) -> AsyncIterator[list[DecisionEvent]]:
        """Every matching event, newest first, in batches of ``batch``: one short tenant session
        per batch, so a long export holds no transaction open."""

        after = None
        while True:
            rows = await self._page(tenant, filters, batch, after)
            if rows:  # validation is CPU work: off the event loop
                yield await asyncio.to_thread(_events, rows, tenant.slug)
            if len(rows) < batch:
                return
            after = (rows[-1].occurred_at, rows[-1].id)

    async def summary(
        self, tenant: TenantInfo, filters: EventFilters = ALL_EVENTS
    ) -> DecisionEventSummary:
        """Counts for charts (tagged-action 0.5): per UTC day and decision, per source, and the
        entity types and policies of the newest events. Default window: the last 30 days."""

        until = filters.until or datetime.now(UTC)
        since = filters.since or until - timedelta(days=SUMMARY_DEFAULT_DAYS)
        filters = replace(filters, since=since, until=until)
        row = DecisionEventRow
        day = self._day(row.occurred_at)
        # One pass for every exact count: per day, decision and source.
        counts_query = self._where(
            select(day, row.action, row.source, func.count()), tenant, filters
        ).group_by(day, row.action, row.source)
        sample_query = (
            self._where(select(row.entities, row.matched), tenant, filters)
            .order_by(row.occurred_at.desc())
            .limit(self._summary_scan + 1)
        )
        async with tenant_session(self._engine, tenant.id) as session:
            count_rows = (await session.execute(counts_query)).all()
            sample = (await session.execute(sample_query)).all()
        days: dict[str, dict[str, int]] = {}
        by_action: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for date, action_, source, count in count_rows:
            actions = days.setdefault(date, {})
            actions[action_] = actions.get(action_, 0) + int(count)
            by_action[action_] = by_action.get(action_, 0) + int(count)
            by_source[source] = by_source.get(source, 0) + int(count)
        truncated = len(sample) > self._summary_scan
        sample = sample[: self._summary_scan]
        entities: dict[str, int] = {}
        policies: dict[tuple[str, str], int] = {}
        for found, matched in sample:
            for type_ in {e.get("type") for e in found or [] if isinstance(e, dict)}:
                if type_:
                    entities[type_] = entities.get(type_, 0) + 1
            for key in {(m.get("kind"), m.get("id")) for m in matched or [] if isinstance(m, dict)}:
                if key[0] and key[1]:
                    policies[key] = policies.get(key, 0) + 1
        top_entities = sorted(entities.items(), key=lambda kv: (-kv[1], kv[0]))[:SUMMARY_TOP]
        top_policies = sorted(policies.items(), key=lambda kv: (-kv[1], kv[0]))[:SUMMARY_TOP]
        return DecisionEventSummary(
            since=since,
            until=until,
            total=sum(by_action.values()),
            by_action=by_action,
            by_source=by_source,
            by_day=[DecisionDay(date=d, actions=a) for d, a in sorted(days.items())],
            top_entities=[EntityCount(type=t, decisions=n) for t, n in top_entities],
            top_policies=[PolicyCount(kind=k, id=i, decisions=n) for (k, i), n in top_policies],
            scanned=len(sample),
            truncated=truncated,
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

    async def requests_used(self, tenant: TenantInfo, start: datetime, end: datetime) -> dict:
        """Requests per quota environment, counted as the engine counts them (ADR-0043 D2)."""

        async with tenant_session(self._engine, tenant.id) as session:
            return await count_requests(session, tenant.id, start, end)

    async def usage_lines(
        self,
        tenant: TenantInfo,
        start: datetime | None,
        end: datetime | None,
        *,
        up_to: int,
    ) -> list[UsageLine]:
        """The ledger per UTC day and meter, source, environment, application, key, provider and
        model (data-export 0.1), oldest first; at most ``up_to`` lines."""

        u = UsageEventRow
        names = ("meter", "source", "environment", "application_id", "key_id", "provider", "model")
        dims = (self._day(u.occurred_at), *(getattr(u, name) for name in names))
        query = select(*dims, func.sum(u.quantity), func.count()).where(u.tenant_id == tenant.id)
        if start is not None:
            query = query.where(u.occurred_at >= start)
        if end is not None:
            query = query.where(u.occurred_at < end)
        query = query.group_by(*dims).order_by(*dims).limit(up_to)
        async with tenant_session(self._engine, tenant.id) as session:
            rows = (await session.execute(query)).all()
        return [
            UsageLine(
                date=date,
                **dict(zip(names, values, strict=True)),
                quantity=int(quantity or 0),
                records=int(count),
            )
            for date, *values, quantity, count in rows
        ]

    async def usage(self, tenant: TenantInfo, month: str | None = None) -> UsageSummary:
        month, start, end = month_bounds(month)
        u = UsageEventRow
        query = (
            select(u.meter, u.source, u.environment, u.application_id, func.sum(u.quantity))
            .where(u.tenant_id == tenant.id, u.occurred_at >= start, u.occurred_at < end)
            .group_by(u.meter, u.source, u.environment, u.application_id)
            .order_by(u.meter, u.source, u.environment, u.application_id)
        )
        day = self._day(u.occurred_at)
        daily_query = (
            select(day, u.meter, func.sum(u.quantity))
            .where(u.tenant_id == tenant.id, u.occurred_at >= start, u.occurred_at < end)
            .group_by(day, u.meter)
        )
        async with tenant_session(self._engine, tenant.id) as session:
            rows = (await session.execute(query)).all()
            daily_rows = (await session.execute(daily_query)).all()
        daily: dict[str, dict[str, int]] = {}
        for date, meter, quantity in daily_rows:
            daily.setdefault(date, {})[meter] = int(quantity or 0)
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
                "daily": [
                    UsageDay(date=d, meters=m).model_dump() for d, m in sorted(daily.items())
                ],
            }
        )


# ------------------------------------------------------------------ retention (ADR-0041 §5)


async def apply_retention(
    engine: AsyncEngine,
    *,
    decision_days: int,
    usage_days: int,
    audit_days: int = 400,
    now: datetime | None = None,
) -> dict[str, int]:
    """Delete records past their retention, tenant by tenant (RLS stays forced for the owner
    role too). Returns the rows deleted per table; logs counts only."""

    now = now or datetime.now(UTC)
    cutoffs = {
        DecisionEventRow: now - timedelta(days=decision_days),
        L1bOutcomeRow: now - timedelta(days=decision_days),
        UsageEventRow: now - timedelta(days=usage_days),
        AuditEventRow: now - timedelta(days=audit_days),
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
    engine: AsyncEngine,
    *,
    decision_days: int,
    usage_days: int,
    audit_days: int,
    interval_s: float,
    webhook_days: int | None = None,
) -> None:
    """Run :func:`apply_retention` now and every ``interval_s``; a failed run is logged and
    retried at the next interval (records are kept longer, never lost early). ``webhook_days``:
    also the webhook delivery log and finished messages (ADR-0048 §4)."""

    while True:
        try:
            await apply_retention(
                engine, decision_days=decision_days, usage_days=usage_days, audit_days=audit_days
            )
            if webhook_days:
                deleted = await apply_webhook_retention(engine, days=webhook_days)
                if deleted:
                    log.info("records.retention table=webhooks deleted=%d", deleted)
        except Exception as exc:
            log.warning("records.retention_failed error=%s", type(exc).__name__)
        await asyncio.sleep(interval_s)
