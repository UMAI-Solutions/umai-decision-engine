"""Browser connections (ADR-0058, contract browser-extension): a person connects their own
browser from the console and gets an evaluate-only API key of the organization's browser
application, with their user id on it; the engine then records the browser's calls with the
person (``source: browser``). The routes are in ``control_plane.app``; this module holds the
rules."""

from __future__ import annotations

import logging
import uuid

from umai_decision_engine.contracts.browser import (
    MAX_CONNECTIONS_PER_PERSON,
    BrowserConnection,
    ConnectionPerson,
)
from umai_decision_engine.contracts.credentials import ApiKeyRecord
from umai_decision_engine.control_plane.keys import KeyService
from umai_decision_engine.db.organizations import UserInfo, UserRepository
from umai_decision_engine.db.repositories import TenantInfo

log = logging.getLogger("umai.control_plane.browser")

CLIENT = "browser"


class BrowserError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


class BrowserService:
    def __init__(self, keys: KeyService, users: UserRepository) -> None:
        self.keys = keys
        self.users = users

    async def _views(self, records: list[ApiKeyRecord]) -> list[BrowserConnection]:
        ids = [i for i in (_uuid(r.user_id) for r in records) if i is not None]
        people = await self.users.by_ids(ids)
        views = []
        for record in records:
            person = people.get(_uuid(record.user_id))  # type: ignore[arg-type]
            views.append(
                BrowserConnection(
                    key_id=record.key_id,
                    tenant_id=record.tenant_id,
                    user=ConnectionPerson(
                        user_id=record.user_id or "",
                        email=person.email if person else None,
                        name=person.name if person else None,
                    ),
                    device_label=record.device_label,
                    application_id=record.application_id,
                    created_at=record.created_at,
                    revoked_at=record.revoked_at,
                    status="revoked" if record.revoked_at else "active",
                )
            )
        return views

    async def view(self, record: ApiKeyRecord) -> BrowserConnection:
        return (await self._views([record]))[0]

    async def connections(
        self, tenant: TenantInfo, *, user_id: uuid.UUID | None = None
    ) -> list[BrowserConnection]:
        """Newest first; one person's when ``user_id`` is given."""

        return await self._views(await self.keys.keys.of_client(tenant, CLIENT, user_id=user_id))

    async def connect(
        self,
        tenant: TenantInfo,
        person: UserInfo,
        *,
        application_id: str,
        device_label: str,
        created_by: str,
    ) -> tuple[BrowserConnection, str, bool]:
        """A connection for ``person`` on the browser application; the caller checked that the
        application exists and is enabled. Refused at the per-person limit."""

        active = [
            k
            for k in await self.keys.keys.of_client(tenant, CLIENT, user_id=person.id)
            if k.revoked_at is None
        ]
        if len(active) >= MAX_CONNECTIONS_PER_PERSON:
            raise BrowserError(
                409,
                "too_many_connections",
                f"you already have {MAX_CONNECTIONS_PER_PERSON} connected browsers here; "
                "disconnect one first",
            )
        label = " ".join(device_label.split())[:120] or "Browser"
        record, secret, published = await self.keys.create(
            tenant,
            name=f"Browser · {label}"[:200],
            scopes=None,
            env="live",
            expires_at=None,
            created_by=created_by,
            application_id=application_id,
            browser_of=(person.id, label),
        )
        log.info(
            "browser.connected tenant=%s key_id=%s application=%s",
            tenant.slug,
            record.key_id,
            application_id,
        )
        return await self.view(record), secret, published

    async def disconnect(
        self, tenant: TenantInfo, key_id: str, *, owner: uuid.UUID | None
    ) -> tuple[BrowserConnection, bool]:
        """Revokes a connection; ``owner`` set: only that person's (404 for anyone else's, so a
        member cannot learn which key ids exist)."""

        record = await self.keys.keys.get(tenant, key_id)
        if (
            record is None
            or record.client != CLIENT
            or (owner is not None and _uuid(record.user_id) != owner)
        ):
            raise BrowserError(404, "not_found", "no such browser connection")
        if record.revoked_at is None:
            record, published = await self.keys.revoke(tenant, key_id)
        else:
            published = False
        log.info("browser.disconnected tenant=%s key_id=%s", tenant.slug, key_id)
        return await self.view(record), published

    async def revoke_person(self, tenant: TenantInfo, user_id: uuid.UUID) -> int:
        """On leaving or removal: every connection of the person in this organization."""

        return len(await self.keys.revoke_browser_of(tenant, user_id))
