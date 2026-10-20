"""The published guardrails: the directory bundle source the engine reads (ADR-0012).

The policy service is the only writer. A new version is written to a hidden temporary
directory and renamed into place, so the engine never sees a half-written version; the active
pointer is replaced atomically. Every publish and activation is appended to
``<bundle_id>/changelog.jsonl`` (ignored by the engine's source) with its actor (ADR-0029).

Guardrails belong to a tenant (policy-bundle 0.8): the default tenant's live directly under the
policy root, every other tenant's under ``tenants/<tenant_id>/``; manifests name the owner.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml

from umai_decision_engine import __version__
from umai_decision_engine.contracts.bundle import PolicyBundle
from umai_decision_engine.policy.manifest import BundleManifest, sha256_hex, sign_manifest
from umai_decision_engine.policy.sources import (
    ACTIVE_FILE,
    BUNDLE_FILE,
    MANIFEST_FILE,
    TENANTS_DIR,
)
from umai_decision_engine.policy.store import parse_bundle

LATEST_CONTRACT = "0.7"  # bundle.yaml contract; manifests carry tenant_id since 0.8
CHANGELOG = "changelog.jsonl"
TESTS_DIR = "tests"


class RepositoryError(Exception):
    pass


def bundle_to_data(bundle: PolicyBundle) -> dict:
    """A bundle as the plain data written to bundle.yaml (derived fields left out)."""

    data = bundle.model_dump(mode="json", by_alias=True, exclude_none=True)
    if data["l0"].get("policies"):
        data["l0"].pop("entities", None)  # derived from the policies
    return data


def bundle_to_yaml(bundle: PolicyBundle, header: str) -> bytes:
    body = yaml.safe_dump(bundle_to_data(bundle), sort_keys=False, allow_unicode=True, width=100)
    return (header + body).encode("utf-8")


class GuardrailRepository:
    def __init__(self, root: Path, owner: str | None = None) -> None:
        self.root = Path(root)
        self.owner = owner  # tenant stamped into manifests; None only for legacy callers

    # ----------------------------------------------------------- reading

    def ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            p.name
            for p in self.root.iterdir()
            if p.is_dir()
            and not p.name.startswith(".")
            and p.name != TENANTS_DIR
            and any(self._version_dirs(p.name))
        )

    def _version_dirs(self, bundle_id: str) -> list[Path]:
        directory = self.root / bundle_id
        if not directory.is_dir():
            return []
        return sorted(
            (p for p in directory.iterdir() if p.is_dir() and p.name.isdigit()),
            key=lambda p: int(p.name),
        )

    def versions(self, bundle_id: str) -> list[int]:
        return [int(p.name) for p in self._version_dirs(bundle_id)]

    def active(self, bundle_id: str) -> int | None:
        pointer = self.root / bundle_id / ACTIVE_FILE
        if not pointer.is_file():
            return None
        raw = pointer.read_text(encoding="utf-8").strip()
        return int(raw) if raw.isdigit() else None

    def version_dir(self, bundle_id: str, version: int) -> Path:
        return self.root / bundle_id / str(version)

    def load(self, bundle_id: str, version: int) -> PolicyBundle:
        path = self.version_dir(bundle_id, version) / BUNDLE_FILE
        if not path.is_file():
            raise RepositoryError(f"{bundle_id} v{version} does not exist")
        return parse_bundle(path.read_bytes())

    def manifest(self, bundle_id: str, version: int) -> BundleManifest | None:
        path = self.version_dir(bundle_id, version) / MANIFEST_FILE
        if not path.is_file():
            return None
        try:
            return BundleManifest.model_validate_json(path.read_bytes())
        except ValueError:  # listed as unsigned; the engine's store reports the broken manifest
            return None

    def changelog(self, bundle_id: str) -> list[dict]:
        path = self.root / bundle_id / CHANGELOG
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    # ----------------------------------------------------------- writing

    def _log(self, bundle_id: str, entry: dict, actor: dict | None) -> None:
        entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **entry, "actor": actor}
        with (self.root / bundle_id / CHANGELOG).open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def publish(
        self,
        bundle: PolicyBundle,
        *,
        base_version: int,
        tests_dir: Path | None,
        note: str | None,
        signing_key: tuple[str, str] | None,
        actor: dict | None = None,
    ) -> int:
        """Write ``bundle`` as the next version of its guardrail; returns the version number."""

        bundle_id = bundle.id
        version = max(self.versions(bundle_id), default=0) + 1
        final = bundle.model_copy(update={"version": version, "contract_version": LATEST_CONTRACT})
        stamp = datetime.now(UTC).replace(microsecond=0)
        header = (
            f"# Published by the policy service (ADR-0022) on {stamp.isoformat()}\n"
            f"# from v{base_version}" + (f": {note}" if note else "") + "\n"
        )
        data = bundle_to_yaml(parse_bundle(bundle_to_yaml(final, "")), header)
        parse_bundle(data)  # the written bytes must load exactly as the engine will load them

        manifest = BundleManifest(
            bundle_id=bundle_id,
            version=version,
            tenant_id=self.owner,
            contract_version=LATEST_CONTRACT,
            engine=f">={__version__}",
            created_at=stamp,
            sha256=sha256_hex(data),
        )
        if signing_key is not None:
            key_id, private_key = signing_key
            manifest = sign_manifest(manifest, private_key, key_id)

        staging = self.root / bundle_id / f".staging-{uuid.uuid4().hex}"
        staging.mkdir(parents=True)
        try:
            (staging / BUNDLE_FILE).write_bytes(data)
            (staging / MANIFEST_FILE).write_text(
                manifest.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8"
            )
            if tests_dir is not None and tests_dir.is_dir():
                shutil.copytree(tests_dir, staging / TESTS_DIR)
            target = self.version_dir(bundle_id, version)
            if target.exists():
                raise RepositoryError(f"{bundle_id} v{version} already exists")
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        self._log(
            bundle_id,
            {
                "action": "publish",
                "version": version,
                "base_version": base_version,
                "note": note,
                "signed": signing_key is not None,
            },
            actor,
        )
        return version

    def activate(
        self, bundle_id: str, version: int, note: str | None = None, actor: dict | None = None
    ) -> int | None:
        if version not in self.versions(bundle_id):
            raise RepositoryError(f"{bundle_id} v{version} does not exist")
        self.load(bundle_id, version)  # must validate
        previous = self.active(bundle_id)
        pointer = self.root / bundle_id / ACTIVE_FILE
        temporary = pointer.with_name(f".{ACTIVE_FILE}-{uuid.uuid4().hex}")
        temporary.write_bytes(f"{version}\n".encode())
        os.replace(temporary, pointer)
        self._log(
            bundle_id,
            {"action": "activate", "version": version, "previous": previous, "note": note},
            actor,
        )
        return previous


class GuardrailRepositories:
    """One repository per tenant over a single policy root (policy-bundle 0.8 layout)."""

    def __init__(self, root: Path, default_tenant: str) -> None:
        self.root = Path(root)
        self.default_tenant = default_tenant

    def for_tenant(self, tenant_id: str) -> GuardrailRepository:
        if tenant_id == self.default_tenant:
            return GuardrailRepository(self.root, owner=tenant_id)
        return GuardrailRepository(self.root / TENANTS_DIR / tenant_id, owner=tenant_id)
