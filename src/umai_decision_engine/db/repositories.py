"""Repositories: the access layer's only query paths. Every tenant-table query filters on
``tenant_id`` explicitly (defence in depth on Postgres, the only guard on SQLite)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from umai_decision_engine.contracts.credentials import (
    DEFAULT_SCOPES,
    ApiKeyRecord,
    KeyEnv,
    KeyRegion,
    KeyRegistryEntry,
    KeyRegistrySnapshot,
    Scope,
)
from umai_decision_engine.db.models import ApiKey, Draft, Tenant
from umai_decision_engine.db.session import global_session, tenant_session
from umai_decision_engine.tenancy.keys import generate_key


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


@dataclass(frozen=True)
class TenantInfo:
    id: uuid.UUID
    slug: str
    name: str
    region: str
    status: str
    created_at: datetime
    edition: str = "community"  # names the plan; entitlements are data (ADR-0019)
    # The terms the creator accepted (organizations 0.3): version, who, when.
    terms_version: str | None = None
    terms_accepted_by: uuid.UUID | None = None
    terms_accepted_at: datetime | None = None
    # An operator's limits over the plan (entitlements 0.1, ADR-0043)
    entitlement_overrides: dict | None = None

    def public(self) -> dict:
        return {
            "id": self.slug,
            "name": self.name,
            "region": self.region,
            "edition": self.edition,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


def tenant_info(row: Tenant) -> TenantInfo:
    return TenantInfo(
        row.id,
        row.slug,
        row.name,
        row.region,
        row.status,
        row.created_at,
        row.edition,
        row.terms_version,
        row.terms_accepted_by,
        row.terms_accepted_at,
        row.entitlement_overrides,
    )


class TenantRepository:
    """Global table: no tenant session."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def create(
        self,
        slug: str,
        name: str,
        region: str,
        *,
        edition: str = "community",
        created_by: uuid.UUID | None = None,
    ) -> TenantInfo:
        async with global_session(self.engine) as session:
            row = Tenant(
                slug=slug,
                name=name,
                region=region,
                status="active",
                edition=edition,
                created_by=created_by,
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise Conflict(f"tenant {slug} already exists") from exc
            return tenant_info(row)

    async def get(self, slug: str) -> TenantInfo | None:
        async with global_session(self.engine) as session:
            row = await session.scalar(select(Tenant).where(Tenant.slug == slug))
            return tenant_info(row) if row else None

    async def require(self, slug: str) -> TenantInfo:
        tenant = await self.get(slug)
        if tenant is None:
            raise NotFound(f"tenant {slug} does not exist")
        return tenant

    async def set_plan(self, slug: str, edition: str, overrides: dict) -> TenantInfo:
        """The tenant's edition and its operator overrides (ADR-0043); empty overrides: none."""

        async with global_session(self.engine) as session:
            row = await session.scalar(select(Tenant).where(Tenant.slug == slug))
            if row is None:
                raise NotFound(f"tenant {slug} does not exist")
            row.edition = edition
            row.entitlement_overrides = overrides or None
            await session.flush()
            return tenant_info(row)

    async def get_or_create(self, slug: str, region: str) -> TenantInfo:
        tenant = await self.get(slug)
        if tenant is not None:
            return tenant
        try:
            return await self.create(slug, slug, region)
        except Conflict:
            return await self.require(slug)

    async def list(self) -> list[TenantInfo]:
        async with global_session(self.engine) as session:
            rows = await session.scalars(select(Tenant).order_by(Tenant.slug))
            return [tenant_info(r) for r in rows]

    async def rename(self, tenant: TenantInfo, name: str) -> TenantInfo:
        async with global_session(self.engine) as session:
            row = await session.scalar(select(Tenant).where(Tenant.id == tenant.id))
            if row is None:
                raise NotFound(f"tenant {tenant.slug} does not exist")
            row.name = name
            return tenant_info(row)


def _record(row: ApiKey, slug: str) -> ApiKeyRecord:
    return ApiKeyRecord(
        key_id=row.key_id,
        tenant_id=slug,
        name=row.name,
        env=row.env,  # type: ignore[arg-type]
        region=row.region,  # type: ignore[arg-type]
        scopes=[Scope(s) for s in row.scopes],
        application_id=row.application_id,
        allowed_ips=row.allowed_ips or None,
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
    )


class ApiKeyRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def create(
        self,
        tenant: TenantInfo,
        *,
        name: str,
        env: KeyEnv,
        region: KeyRegion,
        scopes: list[Scope] | None,
        created_by: str,
        expires_at: datetime | None = None,
        application_id: str | None = None,
        allowed_ips: list[str] | None = None,
    ) -> tuple[ApiKeyRecord, str]:
        """Returns the record and the presentation secret (shown once, never stored).
        ``allowed_ips`` are canonical CIDRs (the caller validated them)."""

        generated = generate_key(env, region)
        row = ApiKey(
            tenant_id=tenant.id,
            key_id=generated.key_id,
            secret_sha256=generated.secret_sha256,
            env=env,
            region=region,
            scopes=[s.value for s in (scopes or list(DEFAULT_SCOPES))],
            application_id=application_id,
            allowed_ips=allowed_ips or None,
            name=name,
            created_by=created_by,
            expires_at=expires_at,
        )
        async with tenant_session(self.engine, tenant.id) as session:
            session.add(row)
            await session.flush()
            return _record(row, tenant.slug), generated.presentation

    async def list(self, tenant: TenantInfo) -> list[ApiKeyRecord]:
        async with tenant_session(self.engine, tenant.id) as session:
            rows = await session.scalars(
                select(ApiKey).where(ApiKey.tenant_id == tenant.id).order_by(ApiKey.created_at)
            )
            return [_record(r, tenant.slug) for r in rows]

    async def revoke(self, tenant: TenantInfo, key_id: str) -> ApiKeyRecord:
        async with tenant_session(self.engine, tenant.id) as session:
            row = await session.scalar(
                select(ApiKey).where(ApiKey.tenant_id == tenant.id, ApiKey.key_id == key_id)
            )
            if row is None:
                raise NotFound(f"key {key_id} does not exist")
            if row.revoked_at is None:
                row.revoked_at = datetime.now(UTC)
                await session.flush()
            return _record(row, tenant.slug)

    async def set_allowed_ips(
        self, tenant: TenantInfo, key_id: str, allowed_ips: list[str] | None
    ) -> ApiKeyRecord:
        """Replace an active key's networks (canonical CIDRs; ``None``: any address)."""

        async with tenant_session(self.engine, tenant.id) as session:
            row = await session.scalar(
                select(ApiKey).where(ApiKey.tenant_id == tenant.id, ApiKey.key_id == key_id)
            )
            if row is None:
                raise NotFound(f"key {key_id} does not exist")
            if row.revoked_at is not None:
                raise Conflict(f"key {key_id} is revoked")
            row.allowed_ips = allowed_ips or None
            await session.flush()
            return _record(row, tenant.slug)

    async def touch(self, tenant: TenantInfo, key_id: str, at: datetime) -> None:
        async with tenant_session(self.engine, tenant.id) as session:
            await session.execute(
                update(ApiKey)
                .where(ApiKey.tenant_id == tenant.id, ApiKey.key_id == key_id)
                .values(last_used_at=at)
            )

    async def snapshot(self, tenant: TenantInfo) -> KeyRegistrySnapshot:
        """The tenant's registry as engines need it (unsigned; the control plane signs)."""

        keys = await self.list(tenant)
        return KeyRegistrySnapshot(
            tenant_id=tenant.slug,
            generated_at=datetime.now(UTC),
            keys=[
                KeyRegistryEntry(
                    key_id=k.key_id,
                    secret_sha256=await self._hash(tenant, k.key_id),
                    env=k.env,
                    region=k.region,
                    scopes=k.scopes,
                    application_id=k.application_id,
                    status="revoked" if k.revoked_at else "active",
                    expires_at=k.expires_at,
                    allowed_ips=k.allowed_ips,
                )
                for k in keys
            ],
        )

    async def _hash(self, tenant: TenantInfo, key_id: str) -> str:
        async with tenant_session(self.engine, tenant.id) as session:
            value = await session.scalar(
                select(ApiKey.secret_sha256).where(
                    ApiKey.tenant_id == tenant.id, ApiKey.key_id == key_id
                )
            )
            if value is None:
                raise NotFound(f"key {key_id} does not exist")
            return value


@dataclass(frozen=True)
class DraftInfo:
    bundle_id: str
    base_version: int
    bundle: dict
    created_at: str
    updated_at: str


def _draft_info(row: Draft) -> DraftInfo:
    return DraftInfo(
        row.bundle_id,
        row.base_version,
        row.bundle,
        row.created_at.isoformat(timespec="seconds"),
        row.updated_at.isoformat(timespec="seconds"),
    )


class DraftRepository:
    """One open draft per guardrail and tenant (replaces the SQLite ``DraftStore``)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def get(self, tenant: TenantInfo, bundle_id: str) -> DraftInfo | None:
        async with tenant_session(self.engine, tenant.id) as session:
            row = await session.scalar(
                select(Draft).where(Draft.tenant_id == tenant.id, Draft.bundle_id == bundle_id)
            )
            return _draft_info(row) if row else None

    async def create(
        self, tenant: TenantInfo, bundle_id: str, base_version: int, bundle: dict
    ) -> DraftInfo:
        async with tenant_session(self.engine, tenant.id) as session:
            row = Draft(
                tenant_id=tenant.id, bundle_id=bundle_id, base_version=base_version, bundle=bundle
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise Conflict(f"guardrail {bundle_id} already has a draft") from exc
            return _draft_info(row)

    async def save(self, tenant: TenantInfo, bundle_id: str, bundle: dict) -> DraftInfo | None:
        async with tenant_session(self.engine, tenant.id) as session:
            row = await session.scalar(
                select(Draft).where(Draft.tenant_id == tenant.id, Draft.bundle_id == bundle_id)
            )
            if row is None:
                return None
            row.bundle = bundle
            row.updated_at = datetime.now(UTC)
            await session.flush()
            return _draft_info(row)

    async def delete(self, tenant: TenantInfo, bundle_id: str) -> bool:
        async with tenant_session(self.engine, tenant.id) as session:
            row = await session.scalar(
                select(Draft).where(Draft.tenant_id == tenant.id, Draft.bundle_id == bundle_id)
            )
            if row is None:
                return False
            await session.delete(row)
            return True

    async def ids(self, tenant: TenantInfo) -> set[str]:
        async with tenant_session(self.engine, tenant.id) as session:
            rows = await session.scalars(
                select(Draft.bundle_id).where(Draft.tenant_id == tenant.id)
            )
            return set(rows)
