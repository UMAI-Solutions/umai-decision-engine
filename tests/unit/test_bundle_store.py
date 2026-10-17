"""Bundle loading, signatures, atomic swap and last-known-good (ADR-0012)."""

from __future__ import annotations

import base64
import json
import os
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.conftest import bundle_yaml
from umai_decision_engine.policy.manifest import BundleManifest, sha256_hex, sign_manifest
from umai_decision_engine.policy.sources import DirectorySource
from umai_decision_engine.policy.store import BundleStore


def _keys() -> tuple[str, str]:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


def _manifest(content: str, private: str | None, *, bundle_id="gr-test", version=1) -> str:
    manifest = BundleManifest(
        bundle_id=bundle_id,
        version=version,
        contract_version="0.3",
        sha256=sha256_hex(content.encode()),
    )
    if private:
        manifest = sign_manifest(manifest, private, "k1")
    return manifest.model_dump_json(exclude_none=True)


def _store(write_bundle, *, require_signed=False, keys=None) -> BundleStore:
    return BundleStore(
        DirectorySource(write_bundle.root), public_keys=keys or {}, require_signed=require_signed
    )


def _touch_later(path) -> None:
    # Make sure the fingerprint changes even on filesystems with coarse mtimes.
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))


def test_unsigned_bundle_loads_in_development(write_bundle) -> None:
    write_bundle(bundle_yaml(), active=1)
    store = _store(write_bundle)
    assert store.reload(force=True)
    loaded = store.get("gr-test")
    assert loaded is not None and not loaded.signed
    assert store.get("gr-test", 1) is loaded
    assert store.state.errors == ()


def test_unsigned_rejected_when_signatures_required(write_bundle) -> None:
    write_bundle(bundle_yaml(), active=1)
    store = _store(write_bundle, require_signed=True)
    store.reload(force=True)
    assert store.get("gr-test") is None
    assert any("manifest.json missing" in e.error for e in store.state.errors)


def test_signed_bundle_verified(write_bundle) -> None:
    private, public = _keys()
    content = bundle_yaml()
    write_bundle(content, active=1, manifest=_manifest(content, private))
    store = _store(write_bundle, require_signed=True, keys={"k1": public})
    store.reload(force=True)
    loaded = store.get("gr-test")
    assert loaded is not None and loaded.signed and loaded.key_id == "k1"


def test_tampered_bundle_rejected(write_bundle) -> None:
    private, public = _keys()
    content = bundle_yaml()
    write_bundle(
        content.replace("ENFORCE", "MONITOR"), active=1, manifest=_manifest(content, private)
    )
    store = _store(write_bundle, require_signed=True, keys={"k1": public})
    store.reload(force=True)
    assert store.get("gr-test") is None
    assert any("sha256" in e.error for e in store.state.errors)


def test_wrong_key_rejected(write_bundle) -> None:
    private, _ = _keys()
    _, other_public = _keys()
    content = bundle_yaml()
    write_bundle(content, active=1, manifest=_manifest(content, private))
    store = _store(write_bundle, require_signed=True, keys={"k1": other_public})
    store.reload(force=True)
    assert store.get("gr-test") is None
    assert any("invalid signature" in e.error for e in store.state.errors)


def test_signature_over_manifest_fields(write_bundle) -> None:
    private, public = _keys()
    content = bundle_yaml()
    manifest = json.loads(_manifest(content, private))
    manifest["version"] = 1
    manifest["engine"] = ">=9"  # changed after signing
    write_bundle(content, active=1, manifest=json.dumps(manifest))
    store = _store(write_bundle, require_signed=True, keys={"k1": public})
    store.reload(force=True)
    assert store.get("gr-test") is None


def test_invalid_new_version_keeps_active(write_bundle) -> None:
    write_bundle(bundle_yaml(version=1), active=1)
    store = _store(write_bundle)
    store.reload(force=True)
    bad = bundle_yaml(version=2).replace("TR_TCKN", "TR_PASSPORT")  # not a supported type
    write_bundle(bad, version=2, active=2)
    store.reload(force=True)
    assert store.get("gr-test").bundle.version == 1
    errors = " ".join(e.error for e in store.state.errors)
    assert "TR_PASSPORT" in errors and "kept 1" in errors


def test_active_pointer_switch_and_rollback(write_bundle) -> None:
    write_bundle(bundle_yaml(version=1), active=1)
    write_bundle(bundle_yaml(version=2, mode="MONITOR"), version=2)
    store = _store(write_bundle)
    store.reload(force=True)
    assert store.get("gr-test").bundle.version == 1

    pointer = write_bundle.root / "gr-test" / "active"
    pointer.write_text("2")
    _touch_later(pointer)
    assert store.reload()
    assert store.get("gr-test").bundle.version == 2

    pointer.write_text("1")
    _touch_later(pointer)
    store.reload()
    assert store.get("gr-test").bundle.version == 1


def test_published_version_is_immutable(write_bundle) -> None:
    directory = write_bundle(bundle_yaml(), active=1)
    store = _store(write_bundle)
    store.reload(force=True)
    sha = store.get("gr-test").sha256
    (directory / "bundle.yaml").write_text(bundle_yaml(mode="MONITOR"))
    _touch_later(directory / "bundle.yaml")
    store.reload()
    assert store.get("gr-test").sha256 == sha
    assert any("modified" in e.error for e in store.state.errors)


def test_no_change_no_reload(write_bundle) -> None:
    write_bundle(bundle_yaml(), active=1)
    store = _store(write_bundle)
    assert store.reload(force=True)
    time.sleep(0.01)
    assert not store.reload()


@pytest.mark.parametrize(
    "mutation, message",
    [
        (
            lambda s: s.replace("l1a:\n  rules: []", "l1a:\n  rules: []\n  hard_block: []"),
            "hard_block",
        ),
        (
            lambda s: s.replace('contract_version: "0.4"', 'contract_version: "0.1"'),
            "contract_version",
        ),
        (lambda s: s.replace("id: gr-test", "id: gr-other"), "do not match"),
    ],
)
def test_invalid_bundles_rejected(write_bundle, mutation, message) -> None:
    write_bundle(mutation(bundle_yaml()), active=1)
    store = _store(write_bundle)
    store.reload(force=True)
    assert store.get("gr-test") is None
    assert any(message in e.error for e in store.state.errors)
