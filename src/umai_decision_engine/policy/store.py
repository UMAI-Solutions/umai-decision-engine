"""Bundle store: validated bundles, atomic swap, last-known-good on failure (ADR-0012).

The only place policy lives in the process. Reloads build a complete new state and swap
one reference, so a request sees either the old state or the new one, never a mix.

Bundles belong to a tenant (ADR-0019 §2, policy-bundle 0.8): lookups take ``(tenant, bundle id)``
and never resolve another tenant's bundle. Bundles without an owner in the source layout belong
to the deployment's default tenant.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

import yaml
from pydantic import ValidationError

from umai_decision_engine.contracts.bundle import PolicyBundle
from umai_decision_engine.policy.manifest import (
    BundleManifest,
    ManifestError,
    sha256_hex,
    verify_manifest,
)
from umai_decision_engine.policy.sources import BundleArtifact, BundleSource
from umai_decision_engine.policy.validate import BundleValidationError, validate_bundle

logger = logging.getLogger("umai.de.policy")


@dataclass(frozen=True)
class LoadedBundle:
    bundle: PolicyBundle
    sha256: str
    signed: bool
    key_id: str | None
    loaded_at: datetime
    location: str
    tenant_id: str | None = None  # owner; None only for inline (playground) bundles


@dataclass(frozen=True)
class LoadError:
    location: str
    error: str
    at: datetime


@dataclass(frozen=True)
class StoreState:
    bundles: Mapping[tuple[str, str, int], LoadedBundle] = field(default_factory=dict)
    active: Mapping[tuple[str, str], int] = field(default_factory=dict)  # (tenant, id) -> version
    errors: tuple[LoadError, ...] = ()
    fingerprint: str | None = None
    reloaded_at: datetime | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def parse_bundle(bundle_bytes: bytes) -> PolicyBundle:
    data = yaml.safe_load(bundle_bytes.decode("utf-8"))
    if not isinstance(data, dict):
        raise BundleValidationError("bundle.yaml must contain a mapping")
    bundle = PolicyBundle.model_validate(data)
    problems = validate_bundle(bundle)
    if problems:
        raise BundleValidationError("; ".join(problems))
    return bundle


class BundleStore:
    def __init__(
        self,
        source: BundleSource,
        *,
        public_keys: dict[str, str],
        require_signed: bool,
        default_tenant: str = "default",
    ) -> None:
        self._source = source
        self.default_tenant = default_tenant
        self._public_keys = dict(public_keys)
        self._require_signed = require_signed
        self._state = StoreState()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> StoreState:
        return self._state

    @property
    def source_name(self) -> str:
        return self._source.name

    # ----------------------------------------------------------- lookup

    def get(
        self, bundle_id: str, version: int | None = None, *, tenant_id: str | None = None
    ) -> LoadedBundle | None:
        """A tenant's bundle (the default tenant when ``tenant_id`` is None); never another's."""

        tenant = tenant_id or self.default_tenant
        state = self._state
        if version is None:
            version = state.active.get((tenant, bundle_id))
            if version is None:
                return None
        return state.bundles.get((tenant, bundle_id, version))

    def owner(self, artifact: BundleArtifact) -> str:
        return artifact.tenant_id or self.default_tenant

    def load_inline(self, bundle: PolicyBundle) -> LoadedBundle:
        """Validate a request-supplied bundle (playground; development only). Not stored."""

        problems = validate_bundle(bundle)
        if problems:
            raise BundleValidationError("; ".join(problems))
        canonical = json.dumps(bundle.model_dump(mode="json", by_alias=True), sort_keys=True)
        return LoadedBundle(
            bundle=bundle,
            sha256=sha256_hex(canonical.encode()),
            signed=False,
            key_id=None,
            loaded_at=_now(),
            location="inline",
        )

    # ----------------------------------------------------------- loading

    def _load(self, artifact: BundleArtifact) -> LoadedBundle:
        signed, key_id = False, None
        owner = self.owner(artifact)
        if artifact.manifest_bytes is not None:
            manifest = BundleManifest.model_validate_json(artifact.manifest_bytes)
            if (manifest.bundle_id, manifest.version) != (artifact.bundle_id, artifact.version):
                raise ManifestError("manifest bundle_id/version do not match the directory")
            if manifest.tenant_id is not None and manifest.tenant_id != owner:
                raise ManifestError(
                    f"manifest tenant_id {manifest.tenant_id!r} does not match the owner {owner!r}"
                )
            signed = verify_manifest(
                manifest,
                artifact.bundle_bytes,
                self._public_keys,
                require_signature=self._require_signed,
            )
            key_id = manifest.key_id if signed else None
        elif self._require_signed:
            raise ManifestError("manifest.json missing; signed bundles are required")

        bundle = parse_bundle(artifact.bundle_bytes)
        if (bundle.id, bundle.version) != (artifact.bundle_id, artifact.version):
            raise BundleValidationError("bundle id/version do not match the directory")
        return LoadedBundle(
            bundle=bundle,
            sha256=sha256_hex(artifact.bundle_bytes),
            signed=signed,
            key_id=key_id,
            loaded_at=_now(),
            location=artifact.location,
            tenant_id=owner,
        )

    def reload(self, *, force: bool = False) -> bool:
        """Rescan the source; returns True when a new state was swapped in."""

        previous = self._state
        fingerprint = self._source.fingerprint()
        if not force and fingerprint == previous.fingerprint:
            return False

        snapshot = self._source.scan()
        errors = [LoadError(loc, msg, _now()) for loc, msg in snapshot.errors]
        bundles: dict[tuple[str, str, int], LoadedBundle] = {}
        seen: set[tuple[str, str, int]] = set()

        for artifact in snapshot.artifacts:
            key = (self.owner(artifact), artifact.bundle_id, artifact.version)
            seen.add(key)
            old = previous.bundles.get(key)
            digest = sha256_hex(artifact.bundle_bytes)
            if old is not None:
                if old.sha256 != digest:
                    errors.append(
                        LoadError(
                            artifact.location,
                            "published version was modified; kept the loaded copy",
                            _now(),
                        )
                    )
                bundles[key] = old
                continue
            try:
                bundles[key] = self._load(artifact)
            except (
                ManifestError,
                BundleValidationError,
                ValidationError,
                ValueError,
                yaml.YAMLError,
            ) as exc:
                errors.append(LoadError(artifact.location, _short(exc), _now()))

        for key, old in previous.bundles.items():
            if key not in seen and previous.active.get(key[:2]) == key[2]:
                bundles[key] = old
                errors.append(
                    LoadError(old.location, "active version removed from source; kept", _now())
                )

        wanted_active = {
            (owner or self.default_tenant, bundle_id): version
            for (owner, bundle_id), version in snapshot.active.items()
        }
        active: dict[tuple[str, str], int] = {}
        for pointer in set(wanted_active) | set(previous.active):
            tenant, bundle_id = pointer
            wanted = wanted_active.get(pointer)
            fallback = previous.active.get(pointer)
            if wanted is not None and (tenant, bundle_id, wanted) in bundles:
                active[pointer] = wanted
                continue
            name = bundle_id if tenant == self.default_tenant else f"{tenant}/{bundle_id}"
            reason = (
                f"active pointer {name} -> {wanted} has no valid bundle"
                if wanted is not None
                else f"active pointer for {name} missing"
            )
            if fallback is not None and (tenant, bundle_id, fallback) in bundles:
                active[pointer] = fallback
                reason += f"; kept {fallback}"
            errors.append(LoadError(name, reason, _now()))

        self._state = StoreState(
            bundles=bundles,
            active=active,
            errors=tuple(errors),
            fingerprint=fingerprint,
            reloaded_at=_now(),
        )
        for error in errors:
            logger.warning("bundle.load_error location=%s error=%s", error.location, error.error)
        logger.info(
            "bundle.reload bundles=%d active=%d tenants=%d errors=%d",
            len(bundles),
            len(active),
            len({tenant for tenant, _ in active}),
            len(errors),
        )
        return True

    async def poll(self, interval_s: float) -> None:
        """Background task: reload when the source fingerprint changes."""

        while True:
            await asyncio.sleep(interval_s)
            try:
                async with self._lock:
                    await asyncio.to_thread(self.reload)
            except Exception:  # never let the poller die
                logger.exception("bundle.poll_failed")


def _short(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]]
        return "schema: " + "; ".join(parts)
    return str(exc)[:500]
