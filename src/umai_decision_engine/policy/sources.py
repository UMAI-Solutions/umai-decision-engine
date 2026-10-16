"""Bundle sources (ADR-0012). The engine reads bundles; it never writes them.

Directory layout::

    <root>/<bundle_id>/active            text file with the active version, e.g. "3"
    <root>/<bundle_id>/<version>/bundle.yaml
    <root>/<bundle_id>/<version>/manifest.json   (optional in development)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

BUNDLE_FILE = "bundle.yaml"
MANIFEST_FILE = "manifest.json"
ACTIVE_FILE = "active"


@dataclass(frozen=True)
class BundleArtifact:
    bundle_id: str
    version: int
    bundle_bytes: bytes
    manifest_bytes: bytes | None
    location: str


@dataclass(frozen=True)
class SourceSnapshot:
    artifacts: list[BundleArtifact] = field(default_factory=list)
    active: dict[str, int] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (location, message)


class BundleSource(Protocol):
    name: str

    def fingerprint(self) -> str:
        """Cheap change detector; equal fingerprints mean nothing to reload."""
        ...

    def scan(self) -> SourceSnapshot: ...


class DirectorySource:
    name = "directory"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _bundle_dirs(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(p for p in self.root.iterdir() if p.is_dir() and not p.name.startswith("."))

    def fingerprint(self) -> str:
        # Content hash, not mtime/size: an active pointer rewritten within one mtime tick with
        # the same size ("1" → "2" → "1") must still be seen. Bundles are small; this is cheap.
        digest = hashlib.sha256()
        for bundle_dir in self._bundle_dirs():
            for path in sorted(bundle_dir.rglob("*")):
                if path.is_file() and path.name in {BUNDLE_FILE, MANIFEST_FILE, ACTIVE_FILE}:
                    digest.update(str(path.relative_to(self.root)).encode() + b"\0")
                    digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    def scan(self) -> SourceSnapshot:
        snapshot = SourceSnapshot()
        for bundle_dir in self._bundle_dirs():
            bundle_id = bundle_dir.name
            active_file = bundle_dir / ACTIVE_FILE
            if active_file.is_file():
                raw = active_file.read_text(encoding="utf-8").strip()
                if raw.isdigit() and int(raw) >= 1:
                    snapshot.active[bundle_id] = int(raw)
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
                        manifest_bytes=manifest_file.read_bytes()
                        if manifest_file.is_file()
                        else None,
                        location=str(version_dir),
                    )
                )
        return snapshot
