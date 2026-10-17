"""API key management (ADR-0023 §2–§6): records in the tenant store, a signed registry
snapshot per tenant published for engines. The control plane is the registry's only writer;
engines never read the database."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from umai_decision_engine.contracts.credentials import (
    CUSTOMER_SCOPES,
    ApiKeyRecord,
    KeyEnv,
    KeyRegistrySnapshot,
    Scope,
)
from umai_decision_engine.db.repositories import ApiKeyRepository, TenantInfo
from umai_decision_engine.tenancy.registry import SNAPSHOT_SUFFIX
from umai_decision_engine.tenancy.snapshot import sign_snapshot

log = logging.getLogger("umai.control_plane.keys")


class KeyPublisher:
    """Writes ``<root>/<tenant>.json`` atomically (temporary file + rename), signed when the
    control plane has a signing key. ``root`` is ``None`` when no engine reads snapshots here
    (then keys are stored but not published, which the API reports)."""

    def __init__(self, root: Path | None, signing_key: tuple[str, str] | None) -> None:
        self.root = Path(root) if root is not None else None
        self.signing_key = signing_key

    def publish(self, snapshot: KeyRegistrySnapshot) -> bool:
        if self.root is None:
            log.warning(
                "key registry dir not configured: snapshot for %s not published", snapshot.tenant_id
            )
            return False
        if self.signing_key is not None:
            key_id, private = self.signing_key
            snapshot = sign_snapshot(snapshot, private, key_id)
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{snapshot.tenant_id}{SNAPSHOT_SUFFIX}"
        temporary = target.with_name(f".{target.name}-{uuid.uuid4().hex}")
        temporary.write_text(
            json.dumps(snapshot.model_dump(mode="json", exclude_none=True), indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
        log.info(
            "key registry published: tenant=%s keys=%d signed=%s",
            snapshot.tenant_id,
            len(snapshot.keys),
            snapshot.signature is not None,
        )
        return True


class KeyService:
    def __init__(self, keys: ApiKeyRepository, publisher: KeyPublisher, region: str) -> None:
        self.keys = keys
        self.publisher = publisher
        self.region = region

    @staticmethod
    def _check_scopes(scopes: list[Scope]) -> None:
        illegal = sorted(s.value for s in scopes if s not in CUSTOMER_SCOPES)
        if illegal:
            raise ValueError(f"customer keys cannot hold scopes: {', '.join(illegal)}")

    async def republish(self, tenant: TenantInfo) -> bool:
        return self.publisher.publish(await self.keys.snapshot(tenant))

    async def create(
        self,
        tenant: TenantInfo,
        *,
        name: str,
        scopes: list[Scope] | None,
        env: KeyEnv,
        expires_at: datetime | None,
        created_by: str,
    ) -> tuple[ApiKeyRecord, str, bool]:
        if scopes:
            self._check_scopes(scopes)
        record, secret = await self.keys.create(
            tenant,
            name=name,
            env=env,
            region=self.region,  # type: ignore[arg-type]
            scopes=scopes,
            created_by=created_by,
            expires_at=expires_at,
        )
        log.info("api key created: tenant=%s key_id=%s env=%s", tenant.slug, record.key_id, env)
        return record, secret, await self.republish(tenant)

    async def list(self, tenant: TenantInfo) -> list[ApiKeyRecord]:
        return await self.keys.list(tenant)

    async def revoke(self, tenant: TenantInfo, key_id: str) -> tuple[ApiKeyRecord, bool]:
        record = await self.keys.revoke(tenant, key_id)
        log.info("api key revoked: tenant=%s key_id=%s", tenant.slug, key_id)
        return record, await self.republish(tenant)
