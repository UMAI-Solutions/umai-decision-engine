"""The audit log (ADR-0045, contract audit 0.1): one event per change through the policy service
and per refused attempt, written to the tenant's append-only ``audit_events`` table.

Routes declare their audit action with a dependency (``audit: Audit = _AUDIT[...]``) and name
what they changed with :meth:`Audit.set`; :class:`AuditRoute` writes the event once the route
has answered, ``success`` for a 2xx and ``denied`` for a 403. A test walks the router and fails
for a changing route without an action, as the permission test does (ADR-0029 §6).

The event is written after the change, in its own transaction: most changes are files, signed
bundles or calls to the payment provider, outside the tenant store. A failed write is logged at
ERROR with the event id (AGENTS rule 4); the change stands, as it already happened."""

from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine

from umai_decision_engine.contracts.audit import (
    CSV_COLUMNS,
    AuditAction,
    AuditActor,
    AuditEvent,
    AuditEventPage,
    AuditTarget,
    Outcome,
    TargetType,
)
from umai_decision_engine.control_plane.authz import TENANT_HEADER, Actor
from umai_decision_engine.db.models import AuditEventRow, User
from umai_decision_engine.db.repositories import TenantInfo
from umai_decision_engine.db.session import global_session, tenant_session

log = logging.getLogger("umai.control_plane.audit")

REQUEST_ID_HEADER = "X-Request-Id"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Changing routes that are not an organization's configuration and have no tenant to record
# under: a person's own console sign-ins (contract console-sessions, ADR-0024 §2 amendment; kept
# in `console_sessions` itself) and the console's session check. Every other changing route
# declares an audit action (`tests/unit/test_audit_routes.py`).
NOT_AUDITED: frozenset[str] = frozenset(
    {
        "POST /v1/me/sessions",
        "DELETE /v1/me/sessions/current",
        "DELETE /v1/me/sessions/{session_id}",
        "POST /v1/me/sessions/revoke-others",
        "POST /v1/sessions/check",
    }
)
MAX_DETAILS = 4096  # bytes of JSON; larger details are replaced by {"truncated": true}
EXPORT_MAX = 50_000  # rows per CSV export, newest first
_EXPORT_BATCH = 1_000

# What each action changes. One target type per action, so a refused attempt names it too.
TARGET: dict[AuditAction, TargetType] = {
    AuditAction.DRAFT_CREATE: "guardrail",
    AuditAction.DRAFT_SAVE: "guardrail",
    AuditAction.DRAFT_DISCARD: "guardrail",
    AuditAction.DRAFT_TEST: "guardrail",
    AuditAction.GUARDRAIL_PUBLISH: "guardrail",
    AuditAction.GUARDRAIL_ACTIVATE: "guardrail",
    AuditAction.TENANT_CREATE: "tenant",
    AuditAction.TENANT_PLAN: "tenant",
    AuditAction.KEY_CREATE: "api_key",
    AuditAction.KEY_REVOKE: "api_key",
    AuditAction.KEY_UPDATE: "api_key",
    AuditAction.KEY_PUBLISH: "tenant",
    AuditAction.ORGANIZATION_CREATE: "organization",
    AuditAction.ORGANIZATION_UPDATE: "organization",
    AuditAction.ORGANIZATION_EXPORT: "organization",
    AuditAction.MEMBER_UPDATE: "member",
    AuditAction.MEMBER_REMOVE: "member",
    AuditAction.MEMBER_LEAVE: "member",
    AuditAction.INVITATION_CREATE: "invitation",
    AuditAction.INVITATION_REVOKE: "invitation",
    AuditAction.INVITATION_RESEND: "invitation",
    AuditAction.INVITATION_ACCEPT: "member",
    AuditAction.APPLICATION_SAVE: "application",
    AuditAction.APPLICATION_DELETE: "application",
    AuditAction.PROVIDER_SAVE: "provider",
    AuditAction.PROVIDER_DELETE: "provider",
    AuditAction.GATEWAY_SETTINGS_UPDATE: "gateway_settings",
    AuditAction.BILLING_ACCOUNT_UPDATE: "billing_account",
    AuditAction.BILLING_CHECKOUT_START: "checkout",
    AuditAction.BILLING_CHECKOUT_COMPLETE: "checkout",
    AuditAction.BILLING_PLAN_CHANGE: "subscription",
    AuditAction.BILLING_CANCEL: "subscription",
    AuditAction.WEBHOOK_CREATE: "webhook_endpoint",
    AuditAction.WEBHOOK_UPDATE: "webhook_endpoint",
    AuditAction.WEBHOOK_DELETE: "webhook_endpoint",
    AuditAction.WEBHOOK_SECRET_ROTATE: "webhook_endpoint",
    AuditAction.WEBHOOK_TEST: "webhook_endpoint",
    AuditAction.AUDIT_EXPORT: "audit_log",
}

# The path parameter that names the target, when the route has one (refused attempts).
_TARGET_PARAM: dict[str, str] = {
    "guardrail": "bundle_id",
    "tenant": "tenant_id",
    "organization": "org_id",
    "api_key": "key_id",
    "member": "user_id",
    "invitation": "invitation_id",
    "application": "application_id",
    "provider": "provider_id",
    "checkout": "checkout_id",
    "webhook_endpoint": "endpoint_id",
}


class Audit:
    """What one request changed; filled by the route, written by :class:`AuditRoute`."""

    def __init__(self, action: AuditAction) -> None:
        self.action = action
        self.tenant: TenantInfo | None = None
        self.target_id: str | None = None
        self.details: dict[str, Any] = {}
        self.named = False

    def set(self, target_id: Any = None, *, tenant: TenantInfo | None = None, **details: Any):
        """The target's id (``None`` for a tenant's single object) and small details: ids and
        before/after values of non-secret fields. ``tenant`` only where the change creates or
        joins one; otherwise the tenant the request acts in."""

        self.target_id = None if target_id is None else str(target_id)
        self.tenant = tenant or self.tenant
        self.details = {k: v for k, v in details.items() if v is not None}
        self.named = True


def audited(action: AuditAction) -> Callable[[Request], Coroutine[Any, Any, Audit]]:
    async def dependency(request: Request) -> Audit:
        audit = Audit(action)
        request.state.audit = audit
        return audit

    dependency.umai_audit = action  # type: ignore[attr-defined]  # read by AuditRoute and tests
    return dependency


def audit_action(dependant) -> AuditAction | None:
    for dep in dependant.dependencies:
        action = getattr(dep.call, "umai_audit", None) or audit_action(dep)
        if action is not None:
            return action
    return None


def before_after(before: Any, after: Any) -> dict[str, Any] | None:
    """``{"before": …, "after": …}`` when the value changed, else nothing."""

    return None if before == after else {"before": before, "after": after}


def _status_and_code(exc: Exception) -> tuple[int | None, str | None]:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        return exc.status_code, detail.get("error") if isinstance(detail, dict) else None
    # OrganizationError, BillingError, ServiceError: their own status (and code)
    return getattr(exc, "status", None), getattr(exc, "code", None)


def request_id(request: Request) -> str:
    cached = getattr(request.state, "request_id", None)
    if cached is None:
        given = request.headers.get(REQUEST_ID_HEADER, "")
        cached = given if _REQUEST_ID.match(given) else f"req-{uuid.uuid4().hex}"
        request.state.request_id = cached
    return cached


class AuditRoute(APIRoute):
    """A route that records its audit event after answering (only routes that declare one)."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        action = audit_action(self.dependant)
        if action is None:
            return handler

        async def audited_handler(request: Request) -> Response:
            rid = request_id(request)
            try:
                response = await handler(request)
            except Exception as exc:
                status, code = _status_and_code(exc)
                if status == 403:
                    await _record(request, action, "denied", {"reason": code or "forbidden"})
                raise
            if response.status_code < 400:
                await _record(request, action, "success", None)
            response.headers[REQUEST_ID_HEADER] = rid
            return response

        return audited_handler


async def _record(
    request: Request, action: AuditAction, outcome: Outcome, details: dict[str, Any] | None
) -> None:
    audit: Audit | None = getattr(request.state, "audit", None)
    actor: Actor | None = getattr(request.state, "actor", None)
    if actor is None:  # no principal (a 401 never reaches here; kept for safety)
        return
    target_type = TARGET[action]
    target_id = None
    if audit is not None and audit.named and outcome == "success":
        target_id, details = audit.target_id, audit.details
    else:
        param = _TARGET_PARAM.get(target_type)
        value = request.path_params.get(param) if param else None
        target_id = None if value is None else str(value)[:255]
        if outcome == "success":
            log.warning("audit.unnamed action=%s path=%s", action, request.url.path)
    tenant = audit.tenant if audit is not None and outcome == "success" else None
    if tenant is None:
        tenant = await _request_tenant(request, actor)
    if tenant is None:  # e.g. a person without an organization yet: nobody to show it to
        log.warning("audit.no_tenant action=%s outcome=%s actor=%s", action, outcome, actor.label)
        return
    await request.app.state.audit.write(
        tenant,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        request_id=request_id(request),
        details=details or {},
    )


async def _request_tenant(request: Request, actor: Actor) -> TenantInfo | None:
    """The tenant the request acts in. A person: always their assertion's tenant (the only one
    they may change; a refused attempt on another is recorded in their own, never written into
    someone else's log). The operator: the path's tenant, else ``X-Umai-Tenant``, else the
    default tenant, as the routes resolve it."""

    if actor.kind == "person":
        slug = actor.tenant_id
    else:
        params = request.path_params
        slug = (
            params.get("tenant_id")
            or params.get("org_id")
            or request.headers.get(TENANT_HEADER)
            or request.app.state.settings.default_tenant_id
        )
    if not slug:
        return None
    return await request.app.state.tenants.get(str(slug))


# ------------------------------------------------------------------ store


class BadCursor(ValueError):
    pass


def _encode_cursor(row: AuditEventRow) -> str:
    raw = f"{row.occurred_at.isoformat()}|{row.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, row_id = raw.split("|", 1)
        return datetime.fromisoformat(at), uuid.UUID(row_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise BadCursor("not a cursor from a previous page") from exc


def _details_json(details: dict[str, Any], action: AuditAction) -> dict[str, Any]:
    data = json.loads(json.dumps(details, default=str))
    if len(json.dumps(data)) > MAX_DETAILS:
        log.warning("audit.details_truncated action=%s", action)
        return {"truncated": True}
    return data


_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> str:
    """A CSV cell a spreadsheet will not run as a formula (OWASP CSV injection)."""

    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA) else text


class AuditLog:
    """Writes and reads the tenant's audit events; reads run in the tenant's session (RLS)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def write(
        self,
        tenant: TenantInfo,
        *,
        actor: Actor,
        action: AuditAction,
        target_type: TargetType,
        target_id: str | None,
        outcome: Outcome,
        request_id: str,
        details: dict[str, Any],
        occurred_at: datetime | None = None,
    ) -> str | None:
        """Append one event; returns its id, or None when the write failed (logged)."""

        event_id = f"au-{uuid.uuid4().hex}"
        try:
            user_id = None
            if actor.kind == "person" and actor.sub:
                async with global_session(self._engine) as session:
                    user_id = await session.scalar(select(User.id).where(User.subject == actor.sub))
            async with tenant_session(self._engine, tenant.id) as session:
                session.add(
                    AuditEventRow(
                        tenant_id=tenant.id,
                        event_id=event_id,
                        occurred_at=occurred_at or datetime.now(UTC),
                        actor_type=actor.kind,
                        actor_sub=actor.sub if actor.kind == "person" else None,
                        actor_user_id=user_id,
                        action=str(action),
                        target_type=target_type,
                        target_id=target_id,
                        outcome=outcome,
                        request_id=request_id,
                        details=_details_json(details, action),
                    )
                )
        except Exception as exc:
            log.error(
                "audit.write_failed event=%s tenant=%s action=%s outcome=%s error=%s",
                event_id,
                tenant.slug,
                action,
                outcome,
                type(exc).__name__,
            )
            return None
        log.info(
            "audit.recorded event=%s tenant=%s action=%s outcome=%s actor=%s",
            event_id,
            tenant.slug,
            action,
            outcome,
            actor.label,
        )
        return event_id

    def _query(self, tenant: TenantInfo, filters: dict[str, Any]):
        row = AuditEventRow
        query = select(row).where(row.tenant_id == tenant.id)
        if filters.get("since") is not None:
            query = query.where(row.occurred_at >= filters["since"])
        if filters.get("until") is not None:
            query = query.where(row.occurred_at < filters["until"])
        actor = filters.get("actor")
        if actor == "operator":
            query = query.where(row.actor_type == "operator")
        elif actor:
            try:
                query = query.where(row.actor_user_id == uuid.UUID(actor))
            except ValueError:
                query = query.where(row.actor_sub == actor)
        action = filters.get("action")
        if action:  # an action, or a prefix of one up to a dot: "billing" matches billing.cancel
            query = query.where(or_(row.action == action, row.action.like(f"{action}.%")))
        for name in ("target_type", "target_id", "outcome"):
            if filters.get(name) is not None:
                query = query.where(getattr(row, name) == filters[name])
        return query

    async def _people(self, rows: list[AuditEventRow]) -> dict[uuid.UUID, tuple[str, str | None]]:
        ids = {r.actor_user_id for r in rows if r.actor_user_id is not None}
        if not ids:
            return {}
        async with global_session(self._engine) as session:
            found = (
                await session.execute(
                    select(User.id, User.email, User.name).where(User.id.in_(ids))
                )
            ).all()
        return {user_id: (email, name) for user_id, email, name in found}

    def _event(self, row: AuditEventRow, tenant: TenantInfo, people: dict) -> AuditEvent:
        email, name = people.get(row.actor_user_id, (None, None))
        return AuditEvent(
            event_id=row.event_id,
            occurred_at=row.occurred_at,
            tenant_id=tenant.slug,
            actor=AuditActor(
                type=row.actor_type,  # type: ignore[arg-type]
                sub=row.actor_sub,
                user_id=row.actor_user_id,
                email=email,
                name=name,
            ),
            action=AuditAction(row.action),
            target=AuditTarget(type=row.target_type, id=row.target_id),  # type: ignore[arg-type]
            outcome=row.outcome,  # type: ignore[arg-type]
            request_id=row.request_id,
            details=row.details or {},
        )

    async def _rows(
        self, tenant: TenantInfo, filters: dict[str, Any], limit: int, cursor: str | None
    ) -> list[AuditEventRow]:
        row = AuditEventRow
        query = self._query(tenant, filters)
        if cursor is not None:
            at, row_id = _decode_cursor(cursor)
            query = query.where(
                or_(row.occurred_at < at, and_(row.occurred_at == at, row.id < row_id))
            )
        query = query.order_by(row.occurred_at.desc(), row.id.desc()).limit(limit)
        async with tenant_session(self._engine, tenant.id) as session:
            return list((await session.scalars(query)).all())

    async def page(
        self, tenant: TenantInfo, *, limit: int = 50, cursor: str | None = None, **filters: Any
    ) -> AuditEventPage:
        rows = await self._rows(tenant, filters, limit + 1, cursor)
        page, more = rows[:limit], len(rows) > limit
        people = await self._people(page)
        return AuditEventPage(
            events=[self._event(r, tenant, people) for r in page],
            next_cursor=_encode_cursor(page[-1]) if more and page else None,
        )

    async def count(self, tenant: TenantInfo, **filters: Any) -> int:
        query = select(func.count()).select_from(self._query(tenant, filters).subquery())
        async with tenant_session(self._engine, tenant.id) as session:
            return int(await session.scalar(query) or 0)

    async def csv_lines(
        self, tenant: TenantInfo, *, limit: int = EXPORT_MAX, **filters: Any
    ) -> AsyncIterator[str]:
        """The matching events as CSV (header first), newest first, at most ``limit`` rows,
        read in batches so a large export never sits in memory."""

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")

        def line(values) -> str:
            buffer.seek(0)
            buffer.truncate()
            writer.writerow(values)
            return buffer.getvalue()

        yield line(CSV_COLUMNS)
        cursor, left = None, limit
        while left > 0:
            rows = await self._rows(tenant, filters, min(_EXPORT_BATCH, left), cursor)
            if not rows:
                return
            people = await self._people(rows)
            for row in rows:
                event = self._event(row, tenant, people)
                yield line(
                    _cell(v)
                    for v in (
                        event.occurred_at.isoformat(),
                        event.event_id,
                        event.actor.type,
                        event.actor.user_id,
                        event.actor.sub,
                        event.actor.email,
                        event.action,
                        event.target.type,
                        event.target.id,
                        event.outcome,
                        event.request_id,
                        json.dumps(event.details, ensure_ascii=False, sort_keys=True)
                        if event.details
                        else "",
                    )
                )
            left -= len(rows)
            cursor = _encode_cursor(rows[-1])
