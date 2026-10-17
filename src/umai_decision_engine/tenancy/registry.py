"""The engine's key registry: per-tenant snapshots loaded like bundles — verify → validate →
atomic swap → hot reload (ADR-0023 §4). No database on the request path. A snapshot that
fails to load keeps the last good one for its tenant and is reported in ``errors``.

Directory layout (the control plane is the only writer)::

    <root>/<tenant_id>.json
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from umai_decision_engine.contracts.credentials import KeyRegistryEntry, KeyRegistrySnapshot
from umai_decision_engine.tenancy.snapshot import SnapshotError, verify_snapshot

log = logging.getLogger("umai.tenancy.registry")

SNAPSHOT_SUFFIX = ".json"


@dataclass(frozen=True)
class RegisteredKey:
    tenant_id: str
    entry: KeyRegistryEntry
    signed: bool

    @property
    def key_id(self) -> str:
        return self.entry.key_id

    def usable_at(self, now: datetime) -> bool:
        if self.entry.status != "active":
            return False
        return self.entry.expires_at is None or self.entry.expires_at > now


@dataclass(frozen=True)
class RegistryState:
    keys: dict[str, RegisteredKey] = field(default_factory=dict)  # key_id → key
    tenants: dict[str, datetime] = field(default_factory=dict)  # tenant → generated_at
    errors: list[tuple[str, str]] = field(default_factory=list)  # (location, message)
    reloaded_at: datetime | None = None


class KeyRegistrySource(Protocol):
    name: str

    def fingerprint(self) -> str: ...

    def scan(self) -> list[tuple[str, bytes]]:
        """(location, snapshot bytes) per tenant."""
        ...


class DirectoryKeyRegistrySource:
    name = "directory"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(
            p
            for p in self.root.iterdir()
            if p.is_file() and p.suffix == SNAPSHOT_SUFFIX and not p.name.startswith(".")
        )

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for path in self._files():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def scan(self) -> list[tuple[str, bytes]]:
        return [(str(path), path.read_bytes()) for path in self._files()]


class KeyRegistry:
    def __init__(
        self,
        source: KeyRegistrySource,
        *,
        public_keys: dict[str, str],
        require_signed: bool,
    ) -> None:
        self.source = source
        self.public_keys = public_keys
        self.require_signed = require_signed
        self._state = RegistryState()
        self._fingerprint: str | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> RegistryState:
        return self._state

    def lookup(self, key_id: str) -> RegisteredKey | None:
        return self._state.keys.get(key_id)

    def reload(self, *, force: bool = False) -> bool:
        """Rescan the source; returns whether anything changed. Thread-safe; the swap is one
        attribute assignment, so readers never see a partial registry."""

        with self._lock:
            fingerprint = self.source.fingerprint()
            if not force and fingerprint == self._fingerprint:
                return False
            previous = self._state
            keys: dict[str, RegisteredKey] = {}
            tenants: dict[str, datetime] = {}
            errors: list[tuple[str, str]] = []
            for location, raw in self.source.scan():
                try:
                    snapshot = KeyRegistrySnapshot.model_validate_json(raw)
                    signed = verify_snapshot(
                        snapshot, self.public_keys, require_signed=self.require_signed
                    )
                except (ValidationError, SnapshotError, ValueError) as exc:
                    errors.append((location, str(exc)[:300]))
                    # keep the tenant's last good keys (last-known-good, as the bundle store)
                    for key in previous.keys.values():
                        if key.tenant_id in previous.tenants and location.endswith(
                            f"{key.tenant_id}{SNAPSHOT_SUFFIX}"
                        ):
                            keys[key.key_id] = key
                            tenants[key.tenant_id] = previous.tenants[key.tenant_id]
                    continue
                tenants[snapshot.tenant_id] = snapshot.generated_at
                for entry in snapshot.keys:
                    if entry.key_id in keys:
                        errors.append((location, f"duplicate key id {entry.key_id}"))
                        continue
                    keys[entry.key_id] = RegisteredKey(snapshot.tenant_id, entry, signed)
            self._state = RegistryState(keys, tenants, errors, datetime.now(UTC))
            self._fingerprint = fingerprint
            for location, message in errors:
                log.warning("key registry: %s: %s", location, message)
            log.info(
                "key registry reloaded: tenants=%d keys=%d errors=%d",
                len(tenants),
                len(keys),
                len(errors),
            )
            return True

    async def poll(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await asyncio.to_thread(self.reload)
            except Exception:  # the previous registry stays in service
                log.exception("key registry reload failed")
