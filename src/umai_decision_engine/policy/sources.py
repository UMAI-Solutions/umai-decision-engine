"""Bundle sources (ADR-0012). The engine reads bundles; it never writes them.

Directory layout::

    <root>/<bundle_id>/active            text file with the active version, e.g. "3"
    <root>/<bundle_id>/<version>/bundle.yaml
    <root>/<bundle_id>/<version>/manifest.json   (optional in development)
    <root>/tenants/<tenant_id>/<bundle_id>/...   the same, owned by <tenant_id> (policy-bundle 0.8)

Bundles directly under ``<root>`` have no owner in the layout; the store gives them to the
deployment's default tenant (ADR-0019 §2).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

BUNDLE_FILE = "bundle.yaml"
MANIFEST_FILE = "manifest.json"
ACTIVE_FILE = "active"
TENANTS_DIR = "tenants"
_TENANT_DIR = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


@dataclass(frozen=True)
class BundleArtifact:
    bundle_id: str
    version: int
    bundle_bytes: bytes
    manifest_bytes: bytes | None
    location: str
    tenant_id: str | None = None  # owner from the layout; None = directly under the root


@dataclass(frozen=True)
class SourceSnapshot:
    artifacts: list[BundleArtifact] = field(default_factory=list)
    # (owner from the layout or None, bundle id) -> active version
    active: dict[tuple[str | None, str], int] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (location, message)


class BundleSource(Protocol):
    name: str

    def fingerprint(self) -> str:
        """Cheap change detector; equal fingerprints mean nothing to reload."""
        ...

    def scan(self) -> SourceSnapshot: ...


class SnapshotSource:
    """A fixed, in-memory snapshot. Loads a chosen set of artifacts through the regular store
    path (manifest check, validation) without a running engine: golden tests (ADR-0015) and,
    later, the control plane's validate/test stages."""

    name = "snapshot"

    def __init__(self, snapshot: SourceSnapshot) -> None:
        self._snapshot = snapshot

    def fingerprint(self) -> str:
        return "snapshot"

    def scan(self) -> SourceSnapshot:
        return self._snapshot


class DirectorySource:
    name = "directory"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @staticmethod
    def _children(base: Path, *, skip_tenants: bool) -> list[Path]:
        if not base.is_dir():
            return []
        return sorted(
            p
            for p in base.iterdir()
            if p.is_dir()
            and not p.name.startswith(".")
            and not (skip_tenants and p.name == TENANTS_DIR)
        )

    def _owners(self) -> list[tuple[str | None, Path]]:
        """``(owner, directory holding bundle directories)``: the root, then each tenant."""

        owners: list[tuple[str | None, Path]] = [(None, self.root)]
        owners += [(t.name, t) for t in self._children(self.root / TENANTS_DIR, skip_tenants=False)]
        return owners

    def fingerprint(self) -> str:
        # Content hash, not mtime/size: an active pointer rewritten within one mtime tick with
        # the same size ("1" → "2" → "1") must still be seen. Bundles are small; this is cheap.
        digest = hashlib.sha256()
        for owner, base in self._owners():
            for bundle_dir in self._children(base, skip_tenants=owner is None):
                for path in sorted(bundle_dir.rglob("*")):
                    if path.is_file() and path.name in {BUNDLE_FILE, MANIFEST_FILE, ACTIVE_FILE}:
                        digest.update(str(path.relative_to(self.root)).encode() + b"\0")
                        digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    def scan(self) -> SourceSnapshot:
        snapshot = SourceSnapshot()
        for owner, base in self._owners():
            if owner is not None and not _TENANT_DIR.match(owner):
                snapshot.errors.append((str(base), f"invalid tenant directory {owner!r}"))
                continue
            for bundle_dir in self._children(base, skip_tenants=owner is None):
                self._scan_bundle(owner, bundle_dir, snapshot)
        return snapshot

    @staticmethod
    def _scan_bundle(owner: str | None, bundle_dir: Path, snapshot: SourceSnapshot) -> None:
        bundle_id = bundle_dir.name
        active_file = bundle_dir / ACTIVE_FILE
        if active_file.is_file():
            raw = active_file.read_text(encoding="utf-8").strip()
            if raw.isdigit() and int(raw) >= 1:
                snapshot.active[(owner, bundle_id)] = int(raw)
            else:
                snapshot.errors.append((str(active_file), f"invalid active version {raw!r}"))
        for version_dir in sorted(p for p in bundle_dir.iterdir() if p.is_dir()):
            if not version_dir.name.isdigit():
                continue
            bundle_file = version_dir / BUNDLE_FILE
            if not bundle_file.is_file():
                snapshot.errors.append((str(version_dir), f"{BUNDLE_FILE} missing"))
                continue
            manifest_file = version_dir / MANIFEST_FILE
            snapshot.artifacts.append(
                BundleArtifact(
                    bundle_id=bundle_id,
                    version=int(version_dir.name),
                    bundle_bytes=bundle_file.read_bytes(),
                    manifest_bytes=manifest_file.read_bytes() if manifest_file.is_file() else None,
                    location=str(version_dir),
                    tenant_id=owner,
                )
            )
