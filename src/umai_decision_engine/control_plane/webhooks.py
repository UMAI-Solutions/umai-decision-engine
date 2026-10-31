"""Outbound webhooks in the policy service (ADR-0048, contract webhooks 0.1): endpoints, the
worker that turns stored records into events, delivery with retries, the delivery log.

Sources are the tenant's own records, read per tenant in its RLS session: blocked decision events
(aggregated per window), the usage ledger against the plan's limits, billing events. Each event
has a deterministic key and is stored once per endpoint (``webhook_messages``), so reading again
after a restart sends nothing twice; the read positions live in memory (one policy service per
stack, like billing reconciliation). Payloads are contract models: ids, types, codes, counts,
amounts and console links, never content (ADR-0005)."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from umai_decision_engine.contracts.webhooks import (
    EVENT_TYPES,
    ApplicationCount,
    BillingPaymentData,
    BillingSubscriptionData,
    DecisionBlockedData,
    DeliveryPage,
    DeliveryView,
    EndpointCreate,
    EndpointCreated,
    EndpointUpdate,
    EndpointView,
    EntityCount,
    Links,
    PolicyCount,
    QuotaThresholdData,
    WebhookEvent,
    WebhooksView,
    WebhookTestData,
    Window,
)
from umai_decision_engine.db.models import (
    BillingEventRow,
    DecisionEventRow,
    SubscriptionRow,
    Tenant,
    WebhookDeliveryRow,
    WebhookEndpointRow,
    WebhookMessageRow,
)
from umai_decision_engine.db.repositories import TenantInfo, TenantRepository
from umai_decision_engine.db.session import global_session, tenant_session
from umai_decision_engine.events.ledger import count_requests
from umai_decision_engine.tenancy.quota import month_bounds
from umai_decision_engine.webhooks.guard import (
    BlockedAddress,
    InvalidUrl,
    Resolver,
    check_url,
    resolve_public,
    system_resolver,
)
from umai_decision_engine.webhooks.keys import (
    UnreadableUrl,
    UrlCipher,
    signature_header,
    signing_secret,
)
from umai_decision_engine.webhooks.sender import Attempt, WebhookSender, masked
from umai_decision_engine.webhooks.slack import slack_message

log = logging.getLogger("umai.webhooks")

BILLING_TYPES = frozenset(t for t in EVENT_TYPES if t.startswith("billing."))
# billing_events.kind → (event type, subscription change); other kinds are not sent
_BILLING_KINDS: dict[str, tuple[str, str | None]] = {
    "payment_succeeded": ("billing.payment_succeeded", None),
    "payment_failed": ("billing.payment_failed", None),
    "checkout_completed": ("billing.subscription_changed", "started"),
    "plan_changed": ("billing.subscription_changed", "plan_changed"),
    "canceled": ("billing.subscription_changed", "canceled"),
    "canceled_by_provider": ("billing.subscription_changed", "canceled"),
    "grace_ended": ("billing.subscription_changed", "grace_ended"),
    "ended": ("billing.subscription_changed", "ended"),
}
_LINKS = {
    "decision.blocked": "/detections",
    "quota.threshold": "/usage",
    "billing.payment_failed": "/billing",
    "billing.payment_succeeded": "/billing",
    "billing.subscription_changed": "/billing",
    "webhook.test": "/webhooks",
}
QUOTA_THRESHOLDS = (80, 100)
BLOCKED_SCAN = 1_000  # newest blocked events whose entities and policies a window counts
TOP = 5
DUE_BATCH = 200  # messages one tick delivers per tenant at most


class WebhookError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class WebhookConfig:
    max_endpoints: int = 10
    retry_delays_s: tuple[int, ...] = (60, 300, 1800, 7200, 21_600)
    blocked_window_s: int = 300
    settle_s: int = 60
    lookback_s: int = 3600
    collect_interval_s: float = 60.0
    disable_after: timedelta = timedelta(hours=24)
    disable_min_failures: int = 5
    concurrency: int = 8
    slack_spacing_s: float = 1.0  # Slack: one message per second per webhook
    rotation_overlap: timedelta = timedelta(hours=24)
    resolve_timeout_s: float = 3.0
    console_url: str | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> WebhookConfig:
        return cls(
            max_endpoints=settings.webhooks_max_endpoints,
            retry_delays_s=tuple(settings.webhooks_retry_delays_s),
            blocked_window_s=settings.webhooks_blocked_window_s,
            settle_s=settings.webhooks_settle_s,
            lookback_s=settings.webhooks_lookback_s,
            collect_interval_s=settings.webhooks_collect_interval_s,
            disable_after=timedelta(hours=settings.webhooks_disable_after_hours),
            disable_min_failures=settings.webhooks_disable_min_failures,
            concurrency=settings.webhooks_concurrency,
            resolve_timeout_s=settings.webhooks_connect_timeout_s,
            console_url=settings.console_url.rstrip("/") if settings.console_url else None,
        )


@dataclass
class _Event:
    type: str
    dedupe_key: str
    occurred_at: datetime
    data: dict[str, Any]


@dataclass
class _Cursors:
    decision: datetime | None = None  # end of the last window read
    billing: datetime | None = None  # the last read's upper bound


def _event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def _floor(when: datetime, seconds: int) -> datetime:
    epoch = int(when.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, UTC)


def _gone(kind: str, status: int | None) -> bool:
    """The receiver says the URL no longer exists: 410; Slack also answers 403 and 404 for a
    revoked hook or a removed or archived channel (reference slack-incoming-webhooks)."""

    return status == 410 or (kind == "slack" and status in (403, 404))


def _encode_cursor(at: datetime, row_id: uuid.UUID) -> str:
    raw = f"{at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, row_id = raw.split("|", 1)
        return datetime.fromisoformat(at), uuid.UUID(row_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise WebhookError(400, "bad_cursor", "not a cursor from a previous page") from exc


def _delivery_view(row: WebhookDeliveryRow) -> DeliveryView:
    return DeliveryView(
        delivery_id=str(row.id),
        endpoint_id=str(row.endpoint_id),
        event_id=row.event_id,
        event_type=row.event_type,  # type: ignore[arg-type]
        attempt=row.attempt,
        attempted_at=row.attempted_at,
        outcome=row.outcome,  # type: ignore[arg-type]
        status_code=row.status_code,
        error=row.error,  # type: ignore[arg-type]
        duration_ms=row.duration_ms,
        next_attempt_at=row.next_attempt_at,
        test=row.test,
    )


class WebhookService:
    def __init__(
        self,
        db: AsyncEngine,
        tenants: TenantRepository,
        plans: Any,  # control_plane.plans.Plans: the tenant's monthly limits
        *,
        master_key: bytes | None,
        sender: WebhookSender | None,
        config: WebhookConfig | None = None,
        resolver: Resolver = system_resolver,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.db = db
        self.tenants = tenants
        self.plans = plans
        self.config = config or WebhookConfig()
        self.sender = sender
        self.resolver = resolver
        self.clock = clock
        self._master = master_key
        self._cipher = UrlCipher(master_key) if master_key is not None else None
        self._active: dict[uuid.UUID, str] = {}  # tenants with an enabled endpoint: id → slug
        self._cursors: dict[uuid.UUID, _Cursors] = {}
        self._collected_at: datetime | None = None
        self._rebuilt_at: datetime | None = None

    @property
    def available(self) -> bool:
        return self._cipher is not None and self.sender is not None

    def _require(self) -> UrlCipher:
        if self._cipher is None or self.sender is None:
            raise WebhookError(
                503, "webhooks_unavailable", "this deployment has no webhook key: none are sent"
            )
        return self._cipher

    # ------------------------------------------------------------------ endpoints

    def _view(self, tenant: TenantInfo, row: WebhookEndpointRow) -> EndpointView:
        url = "…"
        if self._cipher is not None:
            try:
                url = masked(self._cipher.open(row.url_sealed, tenant.id, row.id))
            except UnreadableUrl:
                url = "unreadable: the webhook key changed"
        return EndpointView(
            endpoint_id=str(row.id),
            kind=row.kind,  # type: ignore[arg-type]
            url=url,
            event_types=[t for t in EVENT_TYPES if t in row.event_types],  # type: ignore[misc]
            description=row.description,
            enabled=row.enabled,
            disabled_reason=row.disabled_reason,  # type: ignore[arg-type]
            disabled_at=row.disabled_at,
            signed=row.kind == "generic",
            secret_rotated_at=row.secret_rotated_at,
            consecutive_failures=row.consecutive_failures,
            failing_since=row.failing_since,
            last_success_at=row.last_success_at,
            last_failure_at=row.last_failure_at,
            created_at=row.created_at,
            created_by=row.created_by,
            updated_at=row.updated_at,
        )

    def _secret(self, tenant: TenantInfo, row: WebhookEndpointRow, version: int) -> str:
        assert self._master is not None
        return signing_secret(self._master, tenant.id, row.id, version)

    async def _checked(self, url: str, kind: str) -> str:
        """Registration: the static rules, then the host's addresses when it resolves (a name
        that does not resolve yet is accepted; every attempt checks again)."""

        try:
            check_url(url, kind)
        except InvalidUrl as exc:
            raise WebhookError(422, "invalid_url", str(exc)) from None
        parts = urlsplit(url)
        try:
            await resolve_public(
                parts.hostname or "",
                parts.port or 443,
                self.resolver,
                timeout_s=self.config.resolve_timeout_s,
            )
        except BlockedAddress:
            raise WebhookError(
                422,
                "invalid_url",
                "the host resolves to an address webhooks may not reach (private, loopback, "
                "link-local or metadata)",
            ) from None
        except (OSError, TimeoutError):
            pass
        return url

    async def _row(self, session, tenant: TenantInfo, endpoint_id: str) -> WebhookEndpointRow:
        try:
            key = uuid.UUID(endpoint_id)
        except ValueError:
            key = None
        row = None
        if key is not None:
            row = await session.scalar(
                select(WebhookEndpointRow).where(
                    WebhookEndpointRow.tenant_id == tenant.id, WebhookEndpointRow.id == key
                )
            )
        if row is None:
            raise WebhookError(404, "not_found", "no such webhook endpoint")
        return row

    async def view(self, tenant: TenantInfo) -> WebhooksView:
        async with tenant_session(self.db, tenant.id) as session:
            rows = (
                await session.scalars(
                    select(WebhookEndpointRow)
                    .where(WebhookEndpointRow.tenant_id == tenant.id)
                    .order_by(WebhookEndpointRow.created_at, WebhookEndpointRow.id)
                )
            ).all()
        return WebhooksView(
            tenant_id=tenant.slug,
            available=self.available,
            event_types=list(EVENT_TYPES),  # type: ignore[arg-type]
            max_endpoints=self.config.max_endpoints,
            endpoints=[self._view(tenant, r) for r in rows],
        )

    async def endpoint(self, tenant: TenantInfo, endpoint_id: str) -> EndpointView:
        async with tenant_session(self.db, tenant.id) as session:
            row = await self._row(session, tenant, endpoint_id)
        return self._view(tenant, row)

    async def create(self, tenant: TenantInfo, body: EndpointCreate, actor: str) -> EndpointCreated:
        cipher = self._require()
        url = await self._checked(body.url, body.kind)
        now = self.clock()
        endpoint_id = uuid.uuid4()
        async with tenant_session(self.db, tenant.id) as session:
            count = await session.scalar(
                select(func.count())
                .select_from(WebhookEndpointRow)
                .where(WebhookEndpointRow.tenant_id == tenant.id)
            )
            if (count or 0) >= self.config.max_endpoints:
                raise WebhookError(
                    409,
                    "too_many_endpoints",
                    f"an organization has at most {self.config.max_endpoints} webhook endpoints",
                )
            row = WebhookEndpointRow(
                id=endpoint_id,
                tenant_id=tenant.id,
                url_sealed=cipher.seal(url, tenant.id, endpoint_id),
                kind=body.kind,
                event_types=[t for t in EVENT_TYPES if t in body.event_types],
                description=body.description,
                enabled=body.enabled,
                enabled_since=now,
                disabled_reason=None if body.enabled else "manual",
                disabled_at=None if body.enabled else now,
                secret_version=1,
                consecutive_failures=0,
                created_at=now,
                created_by=actor,
                updated_at=now,
            )
            session.add(row)
        await self._refresh_active(tenant)
        log.info(
            "webhooks.endpoint_created tenant=%s endpoint=%s kind=%s types=%s",
            tenant.slug,
            endpoint_id,
            body.kind,
            ",".join(row.event_types),
        )
        secret = self._secret(tenant, row, 1) if body.kind == "generic" else None
        return EndpointCreated(endpoint=self._view(tenant, row), secret=secret)

    async def update(
        self, tenant: TenantInfo, endpoint_id: str, body: EndpointUpdate, actor: str
    ) -> EndpointView:
        cipher = self._require()
        given = body.model_fields_set
        now = self.clock()
        async with tenant_session(self.db, tenant.id) as session:
            row = await self._row(session, tenant, endpoint_id)
            if "url" in given and body.url is not None:
                url = await self._checked(body.url, row.kind)
                row.url_sealed = cipher.seal(url, tenant.id, row.id)
            if "event_types" in given and body.event_types is not None:
                row.event_types = [t for t in EVENT_TYPES if t in body.event_types]
            if "description" in given:
                row.description = body.description
            if "enabled" in given and body.enabled is not None and body.enabled != row.enabled:
                if body.enabled:
                    row.enabled, row.enabled_since = True, now
                    row.disabled_reason = row.disabled_at = None
                    row.consecutive_failures, row.failing_since = 0, None
                else:
                    self._disable(row, "manual", now)
                    await self._cancel_pending(session, tenant, row.id, now)
            row.updated_at = now
        await self._refresh_active(tenant)
        log.info(
            "webhooks.endpoint_updated tenant=%s endpoint=%s actor=%s fields=%s",
            tenant.slug,
            row.id,
            actor,
            ",".join(sorted(given)),
        )
        return self._view(tenant, row)

    async def delete(self, tenant: TenantInfo, endpoint_id: str, actor: str) -> None:
        async with tenant_session(self.db, tenant.id) as session:
            row = await self._row(session, tenant, endpoint_id)
            await session.execute(
                delete(WebhookMessageRow).where(
                    WebhookMessageRow.tenant_id == tenant.id,
                    WebhookMessageRow.endpoint_id == row.id,
                )
            )
            await session.delete(row)
        await self._refresh_active(tenant)
        log.info(
            "webhooks.endpoint_deleted tenant=%s endpoint=%s actor=%s", tenant.slug, row.id, actor
        )

    async def rotate(self, tenant: TenantInfo, endpoint_id: str, actor: str) -> EndpointCreated:
        self._require()
        now = self.clock()
        async with tenant_session(self.db, tenant.id) as session:
            row = await self._row(session, tenant, endpoint_id)
            if row.kind != "generic":
                raise WebhookError(409, "not_signed", "a Slack endpoint has no signing secret")
            row.secret_version += 1
            row.secret_rotated_at = row.updated_at = now
        log.info(
            "webhooks.secret_rotated tenant=%s endpoint=%s actor=%s", tenant.slug, row.id, actor
        )
        return EndpointCreated(
            endpoint=self._view(tenant, row), secret=self._secret(tenant, row, row.secret_version)
        )

    async def test(self, tenant: TenantInfo, endpoint_id: str, actor: str) -> DeliveryView:
        """One ``webhook.test`` attempt while the caller waits: never retried, and it leaves the
        endpoint's failure streak alone."""

        self._require()
        async with tenant_session(self.db, tenant.id) as session:
            row = await self._row(session, tenant, endpoint_id)
        now = self.clock()
        event_id = _event_id()
        message = WebhookMessageRow(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            endpoint_id=row.id,
            event_id=event_id,
            event_type="webhook.test",
            dedupe_key=f"webhook.test:{event_id}",
            occurred_at=now,
            data=WebhookTestData(endpoint_id=str(row.id)).model_dump(mode="json"),
            status="pending",
            attempts=1,
            created_at=now,
            updated_at=now,
        )
        attempt = await self._send(tenant, row, message, 1, now)
        message.status = "delivered" if attempt.delivered else "failed"
        delivery = self._delivery(tenant, row, message, 1, now, attempt, None, test=True)
        async with tenant_session(self.db, tenant.id) as session:
            session.add(message)
            session.add(delivery)
        log.info(
            "webhooks.test tenant=%s endpoint=%s actor=%s outcome=%s status=%s",
            tenant.slug,
            row.id,
            actor,
            attempt.outcome,
            attempt.status_code,
        )
        return _delivery_view(delivery)

    async def deliveries(
        self,
        tenant: TenantInfo,
        *,
        endpoint_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> DeliveryPage:
        d = WebhookDeliveryRow
        query = select(d).where(d.tenant_id == tenant.id)
        if endpoint_id is not None:
            try:
                query = query.where(d.endpoint_id == uuid.UUID(endpoint_id))
            except ValueError:
                return DeliveryPage(deliveries=[])
        if cursor is not None:
            at, row_id = _decode_cursor(cursor)
            query = query.where(or_(d.attempted_at < at, and_(d.attempted_at == at, d.id < row_id)))
        query = query.order_by(d.attempted_at.desc(), d.id.desc()).limit(limit + 1)
        async with tenant_session(self.db, tenant.id) as session:
            rows = list((await session.scalars(query)).all())
        page, more = rows[:limit], len(rows) > limit
        return DeliveryPage(
            deliveries=[_delivery_view(r) for r in page],
            next_cursor=_encode_cursor(page[-1].attempted_at, page[-1].id)
            if more and page
            else None,
        )

    # ------------------------------------------------------------------ which tenants to read

    async def _refresh_active(self, tenant: TenantInfo) -> None:
        async with tenant_session(self.db, tenant.id) as session:
            enabled = await session.scalar(
                select(func.count())
                .select_from(WebhookEndpointRow)
                .where(WebhookEndpointRow.tenant_id == tenant.id, WebhookEndpointRow.enabled)
            )
        if enabled:
            self._active[tenant.id] = tenant.slug
        else:
            self._active.pop(tenant.id, None)
            self._cursors.pop(tenant.id, None)

    async def rebuild(self) -> int:
        """Find the tenants with an enabled endpoint (at start, then every ten minutes: the
        routes keep the index current in between)."""

        for tenant in await self.tenants.list():
            await self._refresh_active(tenant)
        self._rebuilt_at = self.clock()
        return len(self._active)

    # ------------------------------------------------------------------ reading the records

    async def collect(self, now: datetime | None = None) -> int:
        """Turn new records into messages for every tenant with an enabled endpoint; returns how
        many messages were created."""

        now = now or self.clock()
        created = 0
        for tenant_id, slug in list(self._active.items()):
            tenant = await self.tenants.get(slug)
            if tenant is None:
                self._active.pop(tenant_id, None)
                continue
            try:
                created += await self._collect_tenant(tenant, now)
            except Exception:  # one tenant never stops the others
                log.exception("webhooks.collect_failed tenant=%s", slug)
        return created

    async def _collect_tenant(self, tenant: TenantInfo, now: datetime) -> int:
        async with tenant_session(self.db, tenant.id) as session:
            endpoints = list(
                (
                    await session.scalars(
                        select(WebhookEndpointRow).where(
                            WebhookEndpointRow.tenant_id == tenant.id, WebhookEndpointRow.enabled
                        )
                    )
                ).all()
            )
        if not endpoints:
            self._active.pop(tenant.id, None)
            return 0
        wanted = {t for e in endpoints for t in e.event_types}
        cursors = self._cursors.setdefault(tenant.id, _Cursors())
        events: list[_Event] = []
        # A source nobody subscribes to is skipped as if read: an event type added later
        # starts from then, not from the last hour.
        if "decision.blocked" in wanted:
            events += await self._blocked(tenant, cursors, now)
        else:
            cursors.decision = self._last_window_end(now)
        if "quota.threshold" in wanted:
            events += await self._quota(tenant, now)
        if wanted & BILLING_TYPES:
            events += await self._billing(tenant, cursors, now)
        else:
            cursors.billing = now
        return await self._emit(tenant, endpoints, events, now)

    def _last_window_end(self, now: datetime) -> datetime:
        return _floor(now - timedelta(seconds=self.config.settle_s), self.config.blocked_window_s)

    async def _blocked(self, tenant: TenantInfo, cursors: _Cursors, now: datetime) -> list[_Event]:
        """Complete windows since the last one read: a window is read ``settle_s`` after it ends,
        so records still in a writer's queue are in."""

        size = self.config.blocked_window_s
        last = self._last_window_end(now)
        earliest = _floor(now - timedelta(seconds=self.config.lookback_s), size)
        start = max(cursors.decision or earliest, earliest)
        step = timedelta(seconds=size)
        events = []
        while start + step <= last:
            data = await self._window(tenant, start, start + step)
            if data is not None:
                events.append(
                    _Event(
                        "decision.blocked",
                        f"decision.blocked:{start.isoformat()}",
                        start + step,
                        data,
                    )
                )
            start += step
        cursors.decision = start
        return events

    async def _window(self, tenant: TenantInfo, start: datetime, end: datetime) -> dict | None:
        row = DecisionEventRow
        where = (
            row.tenant_id == tenant.id,
            row.action == "BLOCK",
            row.occurred_at >= start,
            row.occurred_at < end,
        )
        async with tenant_session(self.db, tenant.id) as session:
            groups = (
                await session.execute(
                    select(row.source, row.phase, row.application_id, func.count())
                    .where(*where)
                    .group_by(row.source, row.phase, row.application_id)
                )
            ).all()
            if not groups:
                return None
            sample = (
                await session.execute(
                    select(row.event_id, row.entities, row.matched)
                    .where(*where)
                    .order_by(row.occurred_at.desc(), row.id.desc())
                    .limit(BLOCKED_SCAN + 1)
                )
            ).all()
        by_source: dict[str, int] = {}
        by_phase: dict[str, int] = {}
        apps: dict[str | None, int] = {}
        for source, phase, application_id, count in groups:
            by_source[source] = by_source.get(source, 0) + int(count)
            by_phase[phase] = by_phase.get(phase, 0) + int(count)
            apps[application_id] = apps.get(application_id, 0) + int(count)
        truncated = len(sample) > BLOCKED_SCAN
        sample = sample[:BLOCKED_SCAN]
        entities: dict[str, int] = {}
        policies: dict[tuple[str, str], int] = {}
        for _, found, matched in sample:
            for type_ in {e.get("type") for e in found or [] if isinstance(e, dict)}:
                if type_:
                    entities[type_] = entities.get(type_, 0) + 1
            for key in {(m.get("kind"), m.get("id")) for m in matched or [] if isinstance(m, dict)}:
                if key[0] and key[1]:
                    policies[key] = policies.get(key, 0) + 1

        def top(counts: dict) -> list:
            return sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:TOP]

        data = DecisionBlockedData(
            window=Window(from_=start, to=end),
            blocked=sum(by_source.values()),
            by_source=by_source,  # type: ignore[arg-type]
            by_phase=by_phase,
            applications=[ApplicationCount(application_id=a, blocked=n) for a, n in top(apps)],
            top_entities=[EntityCount(type=t, decisions=n) for t, n in top(entities)],
            top_policies=[PolicyCount(kind=k, id=i, decisions=n) for (k, i), n in top(policies)],
            sample_event_ids=[event_id for event_id, _, _ in sample[:TOP]],
            scanned=len(sample),
            truncated=truncated,
        )
        return data.model_dump(mode="json", by_alias=True)

    async def _quota(self, tenant: TenantInfo, now: datetime) -> list[_Event]:
        """The month's state against the plan: each threshold once per month, environment and
        limit (a new limit can be reported again)."""

        limits = self.plans.limits(tenant)
        checked = [(env, limits.monthly(env)) for env in ("live", "test")]
        checked = [(env, limit) for env, limit in checked if limit]
        if not checked:
            return []
        month, start, end = month_bounds(None, now)
        async with tenant_session(self.db, tenant.id) as session:
            used = await count_requests(session, tenant.id, start, end)
        events = []
        for env, limit in checked:
            for threshold in QUOTA_THRESHOLDS:
                if used[env] * 100 < threshold * limit:
                    continue
                data = QuotaThresholdData(
                    environment=env,  # type: ignore[arg-type]
                    threshold_percent=threshold,  # type: ignore[arg-type]
                    limit=limit,
                    used=used[env],
                    month=month,
                    resets_at=end,
                    edition=tenant.edition,  # type: ignore[arg-type]
                )
                key = f"quota.threshold:{month}:{env}:{threshold}:{limit}"
                events.append(_Event("quota.threshold", key, now, data.model_dump(mode="json")))
        return events

    async def _billing(self, tenant: TenantInfo, cursors: _Cursors, now: datetime) -> list[_Event]:
        """New billing events; the window overlaps the previous read by ``settle_s`` (a row is
        stamped before its transaction commits) and the event's id keeps it from repeating."""

        since = max(
            cursors.billing or now - timedelta(seconds=self.config.lookback_s),
            now - timedelta(seconds=self.config.lookback_s),
        ) - timedelta(seconds=self.config.settle_s)
        b = BillingEventRow
        async with tenant_session(self.db, tenant.id) as session:
            rows = (
                await session.scalars(
                    select(b)
                    .where(
                        b.tenant_id == tenant.id,
                        b.occurred_at >= since,
                        b.occurred_at < now,
                        b.kind.in_(list(_BILLING_KINDS)),
                    )
                    .order_by(b.occurred_at, b.id)
                )
            ).all()
            subscription = await session.scalar(
                select(SubscriptionRow).where(SubscriptionRow.tenant_id == tenant.id)
            )
        cursors.billing = now
        events = []
        for row in rows:
            type_, change = _BILLING_KINDS[row.kind]
            detail = row.detail or {}
            if row.kind == "checkout_completed" and detail.get("status") not in (
                "active",
                "pending",
            ):
                continue
            status = subscription.status if subscription is not None else None
            edition = subscription.edition if subscription is not None else None
            try:
                if change is None:
                    data = BillingPaymentData(
                        amount=row.amount,
                        currency=row.currency,
                        edition=edition,  # type: ignore[arg-type]
                        subscription_status=status,
                        grace_until=(
                            subscription.grace_until
                            if type_ == "billing.payment_failed" and subscription is not None
                            else None
                        ),
                    )
                else:
                    data = BillingSubscriptionData(
                        change=change,  # type: ignore[arg-type]
                        edition=detail.get("edition") or edition,
                        effective=detail.get("when") if change == "plan_changed" else None,
                        subscription_status=status,
                    )
            except ValidationError:
                log.warning(
                    "webhooks.billing_event_skipped tenant=%s kind=%s", tenant.slug, row.kind
                )
                continue
            events.append(
                _Event(type_, f"billing:{row.id}", row.occurred_at, data.model_dump(mode="json"))
            )
        return events

    async def _emit(
        self,
        tenant: TenantInfo,
        endpoints: list[WebhookEndpointRow],
        events: list[_Event],
        now: datetime,
    ) -> int:
        """One message per event and subscribed endpoint, unless that key was stored before; an
        endpoint gets only what happened while it was enabled."""

        if not events:
            return 0
        m = WebhookMessageRow
        created = 0
        async with tenant_session(self.db, tenant.id) as session:
            stored = {
                (endpoint_id, key)
                for endpoint_id, key in (
                    await session.execute(
                        select(m.endpoint_id, m.dedupe_key).where(
                            m.tenant_id == tenant.id,
                            m.dedupe_key.in_([e.dedupe_key for e in events]),
                        )
                    )
                ).all()
            }
            for event in events:
                event_id = _event_id()  # one id for every endpoint that receives this event
                for endpoint in endpoints:
                    if event.type not in endpoint.event_types:
                        continue
                    if event.occurred_at < endpoint.enabled_since:
                        continue
                    if (endpoint.id, event.dedupe_key) in stored:
                        continue
                    session.add(
                        m(
                            tenant_id=tenant.id,
                            endpoint_id=endpoint.id,
                            event_id=event_id,
                            event_type=event.type,
                            dedupe_key=event.dedupe_key,
                            occurred_at=event.occurred_at,
                            data=event.data,
                            status="pending",
                            attempts=0,
                            next_attempt_at=now,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    created += 1
        if created:
            log.info("webhooks.collected tenant=%s messages=%d", tenant.slug, created)
        return created

    # ------------------------------------------------------------------ delivering

    def _event(self, tenant: TenantInfo, message: WebhookMessageRow) -> WebhookEvent:
        path = _LINKS.get(message.event_type)
        console = f"{self.config.console_url}{path}" if self.config.console_url and path else None
        return WebhookEvent(
            event_id=message.event_id,
            type=message.event_type,  # type: ignore[arg-type]
            occurred_at=message.occurred_at,
            organization_id=tenant.slug,
            data=message.data,
            links=Links(console=console),
        )

    async def _send(
        self,
        tenant: TenantInfo,
        endpoint: WebhookEndpointRow,
        message: WebhookMessageRow,
        attempt: int,
        now: datetime,
    ) -> Attempt:
        assert self._cipher is not None and self.sender is not None
        try:
            url = check_url(
                self._cipher.open(endpoint.url_sealed, tenant.id, endpoint.id), endpoint.kind
            )
        except (UnreadableUrl, InvalidUrl):
            log.warning("webhooks.unusable_url tenant=%s endpoint=%s", tenant.slug, endpoint.id)
            return Attempt("failed", None, "blocked_address", 0.0)
        event = self._event(tenant, message)
        if endpoint.kind == "slack":
            body = json.dumps(slack_message(event), separators=(",", ":")).encode()
            headers = {"content-type": "application/json"}
        else:
            body = event.model_dump_json(by_alias=True).encode()
            secrets = [self._secret(tenant, endpoint, endpoint.secret_version)]
            rotated = endpoint.secret_rotated_at
            if rotated is not None and now - rotated < self.config.rotation_overlap:
                secrets.append(self._secret(tenant, endpoint, endpoint.secret_version - 1))
            headers = {
                "content-type": "application/json",
                "x-umai-event-id": event.event_id,
                "x-umai-event-type": event.type,
                "x-umai-delivery-attempt": str(attempt),
                "x-umai-signature": signature_header(secrets, body, int(now.timestamp())),
            }
        return await self.sender.post(url, body, headers)

    def _delivery(
        self,
        tenant: TenantInfo,
        endpoint: WebhookEndpointRow,
        message: WebhookMessageRow,
        attempt_no: int,
        at: datetime,
        attempt: Attempt,
        next_attempt_at: datetime | None,
        *,
        test: bool = False,
    ) -> WebhookDeliveryRow:
        return WebhookDeliveryRow(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            endpoint_id=endpoint.id,
            message_id=message.id,
            event_id=message.event_id,
            event_type=message.event_type,
            attempt=attempt_no,
            attempted_at=at,
            outcome=attempt.outcome,
            status_code=attempt.status_code,
            error=attempt.error,
            duration_ms=attempt.duration_ms,
            next_attempt_at=next_attempt_at,
            test=test,
        )

    @staticmethod
    def _disable(row: WebhookEndpointRow, reason: str, now: datetime) -> None:
        row.enabled = False
        row.disabled_reason = reason
        row.disabled_at = row.updated_at = now

    async def _cancel_pending(self, session, tenant: TenantInfo, endpoint: uuid.UUID, now) -> None:
        await session.execute(
            update(WebhookMessageRow)
            .where(
                WebhookMessageRow.tenant_id == tenant.id,
                WebhookMessageRow.endpoint_id == endpoint,
                WebhookMessageRow.status == "pending",
            )
            .values(status="canceled", next_attempt_at=None, updated_at=now)
        )

    async def deliver_due(self, now: datetime | None = None) -> int:
        """Attempt every message whose time has come; returns the number of attempts."""

        if not self.available:
            return 0
        now = now or self.clock()
        attempts = 0
        for tenant_id, slug in list(self._active.items()):
            tenant = await self.tenants.get(slug)
            if tenant is None:
                self._active.pop(tenant_id, None)
                continue
            try:
                attempts += await self._deliver_tenant(tenant, now)
            except Exception:
                log.exception("webhooks.deliver_failed tenant=%s", slug)
        return attempts

    async def _deliver_tenant(self, tenant: TenantInfo, now: datetime) -> int:
        m, e = WebhookMessageRow, WebhookEndpointRow
        async with tenant_session(self.db, tenant.id) as session:
            due = (
                await session.execute(
                    select(m, e)
                    .join(e, and_(e.id == m.endpoint_id, e.tenant_id == m.tenant_id))
                    .where(
                        m.tenant_id == tenant.id,
                        m.status == "pending",
                        m.next_attempt_at <= now,
                        e.enabled,
                    )
                    .order_by(m.next_attempt_at, m.created_at)
                    .limit(DUE_BATCH)
                )
            ).all()
        if not due:
            return 0
        groups: dict[uuid.UUID, tuple[WebhookEndpointRow, list[WebhookMessageRow]]] = {}
        for message, endpoint in due:
            groups.setdefault(endpoint.id, (endpoint, []))[1].append(message)
        gate = asyncio.Semaphore(self.config.concurrency)
        made = 0

        async def one_endpoint(endpoint: WebhookEndpointRow, messages: list[WebhookMessageRow]):
            nonlocal made
            async with gate:
                for index, message in enumerate(messages):
                    if index and endpoint.kind == "slack" and self.config.slack_spacing_s:
                        await asyncio.sleep(self.config.slack_spacing_s)
                    at = self.clock()
                    attempt = await self._send(tenant, endpoint, message, message.attempts + 1, at)
                    made += 1
                    if await self._record(tenant, endpoint, message, attempt, at):
                        break  # turned off: the rest were cancelled

        await asyncio.gather(*(one_endpoint(ep, ms) for ep, ms in groups.values()))
        return made

    async def _record(
        self,
        tenant: TenantInfo,
        endpoint: WebhookEndpointRow,
        message: WebhookMessageRow,
        attempt: Attempt,
        at: datetime,
    ) -> bool:
        """Store the attempt, schedule the retry, update the endpoint's streak; ``True`` when
        the endpoint was turned off."""

        delays = self.config.retry_delays_s
        disabled = False
        async with tenant_session(self.db, tenant.id) as session:
            msg = await session.get(WebhookMessageRow, message.id)
            row = await session.get(WebhookEndpointRow, endpoint.id)
            if msg is None or row is None or msg.tenant_id != tenant.id:
                return False  # deleted meanwhile
            number = msg.attempts + 1
            msg.attempts = number
            msg.updated_at = at
            next_at = None
            if attempt.delivered:
                msg.status, msg.next_attempt_at = "delivered", None
                row.consecutive_failures, row.failing_since = 0, None
                row.last_success_at = at
            else:
                if number <= len(delays) and row.enabled:
                    next_at = at + timedelta(seconds=delays[number - 1])
                    msg.next_attempt_at = next_at
                else:
                    msg.status = "failed" if row.enabled else "canceled"
                    msg.next_attempt_at = None
                row.consecutive_failures += 1
                row.failing_since = row.failing_since or at
                row.last_failure_at = at
                reason = None
                if _gone(row.kind, attempt.status_code):
                    reason = "gone"
                elif (
                    row.consecutive_failures >= self.config.disable_min_failures
                    and at - row.failing_since >= self.config.disable_after
                ):
                    reason = "failing"
                if reason is not None and row.enabled:
                    self._disable(row, reason, at)
                    await self._cancel_pending(session, tenant, row.id, at)
                    if next_at is not None:
                        msg.status, msg.next_attempt_at, next_at = "canceled", None, None
                    disabled = True
            session.add(self._delivery(tenant, row, msg, number, at, attempt, next_at))
        if disabled:
            log.warning(
                "webhooks.endpoint_disabled tenant=%s endpoint=%s reason=%s failures=%d",
                tenant.slug,
                endpoint.id,
                row.disabled_reason,
                row.consecutive_failures,
            )
            await self._refresh_active(tenant)
        return disabled

    # ------------------------------------------------------------------ the worker

    async def tick(self) -> None:
        now = self.clock()
        if self._rebuilt_at is None or now - self._rebuilt_at >= timedelta(minutes=10):
            await self.rebuild()
        if self._collected_at is None or (now - self._collected_at).total_seconds() >= (
            self.config.collect_interval_s
        ):
            self._collected_at = now
            await self.collect(now)
        await self.deliver_due(now)

    async def run(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self.tick()
            except Exception:  # the next tick tries again
                log.exception("webhooks tick failed")


async def apply_webhook_retention(
    engine: AsyncEngine, *, days: int, now: datetime | None = None
) -> int:
    """Delete delivery-log rows and finished messages past retention, tenant by tenant, with the
    owner role (the application role cannot delete the log). Returns the rows deleted."""

    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    async with global_session(engine) as session:
        tenants = [row[0] for row in (await session.execute(select(Tenant.id))).all()]
    deleted = 0
    for tenant_id in tenants:
        async with tenant_session(engine, tenant_id) as session:
            for statement in (
                delete(WebhookDeliveryRow).where(
                    WebhookDeliveryRow.tenant_id == tenant_id,
                    WebhookDeliveryRow.attempted_at < cutoff,
                ),
                delete(WebhookMessageRow).where(
                    WebhookMessageRow.tenant_id == tenant_id,
                    WebhookMessageRow.status != "pending",
                    WebhookMessageRow.updated_at < cutoff,
                ),
            ):
                result = await session.execute(statement)
                deleted += result.rowcount or 0
    return deleted
