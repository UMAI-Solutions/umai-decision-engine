"""API keys, registry snapshots, admission and rate limiting (ADR-0023). Synthetic only."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from umai_decision_engine.contracts.credentials import (
    KeyRegistryEntry,
    KeyRegistrySnapshot,
    Scope,
)
from umai_decision_engine.tenancy.admission import Admission
from umai_decision_engine.tenancy.keys import generate_key, parse_key, sha256_hex
from umai_decision_engine.tenancy.limits import RateLimiter
from umai_decision_engine.tenancy.registry import DirectoryKeyRegistrySource, KeyRegistry
from umai_decision_engine.tenancy.snapshot import SnapshotError, sign_snapshot, verify_snapshot


def _keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode(), base64.b64encode(public).decode()


def _snapshot(tenant: str, *entries: KeyRegistryEntry) -> KeyRegistrySnapshot:
    return KeyRegistrySnapshot(tenant_id=tenant, generated_at=datetime.now(UTC), keys=list(entries))


def _entry(generated, scopes=(Scope.EVALUATE,), status="active", expires_at=None):
    return KeyRegistryEntry(
        key_id=generated.key_id,
        secret_sha256=generated.secret_sha256,
        env="live",
        region="sh",
        scopes=list(scopes),
        status=status,
        expires_at=expires_at,
    )


# ------------------------------------------------------------ key format


def test_generated_key_round_trips_and_only_the_hash_is_kept() -> None:
    generated = generate_key("live", "eu")
    assert generated.presentation.startswith("umai_live_eu_")
    parsed = parse_key(generated.presentation)
    assert parsed is not None
    assert parsed.key_id == generated.key_id
    assert parsed.secret_sha256 == generated.secret_sha256
    assert generated.presentation.rsplit("_", 1)[1] not in generated.secret_sha256
    assert sha256_hex("x") != sha256_hex("y")


@pytest.mark.parametrize(
    "value",
    ["", "bearer", "umai_live_eu_short", "umai_prod_eu_ABCDEFGH_" + "a" * 43, "not a key at all"],
)
def test_parse_rejects_other_shapes(value: str) -> None:
    assert parse_key(value) is None


def test_two_keys_never_collide() -> None:
    assert generate_key("test", "sh").key_id != generate_key("test", "sh").key_id


# ------------------------------------------------------------ snapshots


def test_snapshot_signature_verifies_and_tampering_is_detected() -> None:
    private, public = _keypair()
    generated = generate_key("live", "sh")
    signed = sign_snapshot(_snapshot("t", _entry(generated)), private, "k1")
    assert verify_snapshot(signed, {"k1": public}, require_signed=True) is True
    tampered = signed.model_copy(
        update={"keys": [_entry(generated, scopes=(Scope.EVALUATE, Scope.BUNDLES_RELOAD))]}
    )
    with pytest.raises(SnapshotError):
        verify_snapshot(tampered, {"k1": public}, require_signed=True)
    with pytest.raises(SnapshotError):
        verify_snapshot(signed, {"other": public}, require_signed=True)


def test_unsigned_snapshot_only_in_development() -> None:
    snapshot = _snapshot("t")
    assert verify_snapshot(snapshot, {}, require_signed=False) is False
    with pytest.raises(SnapshotError):
        verify_snapshot(snapshot, {}, require_signed=True)


# ------------------------------------------------------------ registry


def _write(root, snapshot: KeyRegistrySnapshot) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{snapshot.tenant_id}.json").write_text(
        json.dumps(snapshot.model_dump(mode="json", exclude_none=True)), encoding="utf-8"
    )


def test_registry_loads_signed_snapshots_and_keeps_last_good_on_error(tmp_path) -> None:
    private, public = _keypair()
    root = tmp_path / "keys"
    a, b = generate_key("live", "sh"), generate_key("live", "sh")
    _write(root, sign_snapshot(_snapshot("tenant-a", _entry(a)), private, "k1"))
    _write(root, sign_snapshot(_snapshot("tenant-b", _entry(b)), private, "k1"))
    registry = KeyRegistry(
        DirectoryKeyRegistrySource(root), public_keys={"k1": public}, require_signed=True
    )
    assert registry.reload(force=True) is True
    assert registry.lookup(a.key_id).tenant_id == "tenant-a"
    assert registry.lookup(b.key_id).tenant_id == "tenant-b"
    assert registry.reload() is False  # nothing changed

    # tenant-b's file becomes unsigned garbage: its last good keys stay, the error is reported
    (root / "tenant-b.json").write_text(
        json.dumps(_snapshot("tenant-b").model_dump(mode="json", exclude_none=True)),
        encoding="utf-8",
    )
    assert registry.reload() is True
    assert registry.lookup(b.key_id) is not None
    assert any("tenant-b.json" in location for location, _ in registry.state.errors)

    # revocation reaches the engine on the next reload
    _write(root, sign_snapshot(_snapshot("tenant-a", _entry(a, status="revoked")), private, "k1"))
    registry.reload()
    assert not registry.lookup(a.key_id).usable_at(datetime.now(UTC))


# ------------------------------------------------------------ admission


def _admission(registry: KeyRegistry | None = None, shared: str | None = None) -> Admission:
    return Admission(shared_token=shared, registry=registry, limiter=None, development=False)


def _registry_with(*entries: KeyRegistryEntry, tenant: str = "tenant-a") -> KeyRegistry:
    class Source:
        name = "memory"

        def fingerprint(self) -> str:
            return "x"

        def scan(self):
            payload = _snapshot(tenant, *entries).model_dump_json(exclude_none=True)
            return [("memory", payload.encode())]

    registry = KeyRegistry(Source(), public_keys={}, require_signed=False)
    registry.reload(force=True)
    return registry


def test_admission_resolves_tenant_and_scopes_from_the_key() -> None:
    generated = generate_key("live", "sh")
    admission = _admission(_registry_with(_entry(generated, scopes=(Scope.EVALUATE,))))
    principal = admission.authenticate(f"Bearer {generated.presentation}")
    assert principal.kind == "api_key"
    assert principal.tenant_id == "tenant-a"
    assert principal.scopes == {Scope.EVALUATE}
    assert principal.bind_tenant(None) == "tenant-a"
    assert principal.bind_tenant("tenant-a") == "tenant-a"
    with pytest.raises(HTTPException) as exc:
        principal.bind_tenant("tenant-b")
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "tenant_mismatch"


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Basic abc",
        "Bearer wrong-shared-token",
        "Bearer umai_live_sh_AAAAAAAA_" + "b" * 43,
    ],
)
def test_admission_rejects_unknown_credentials(header: str | None) -> None:
    generated = generate_key("live", "sh")
    admission = _admission(_registry_with(_entry(generated)), shared="shared-secret")
    with pytest.raises(HTTPException) as exc:
        admission.authenticate(header)
    assert exc.value.status_code == 401
    assert exc.value.headers["WWW-Authenticate"] == "Bearer"


def test_admission_wrong_secret_for_known_key_id_is_401() -> None:
    generated = generate_key("live", "sh")
    admission = _admission(_registry_with(_entry(generated)))
    forged = generated.presentation[:-5] + "zzzzz"
    with pytest.raises(HTTPException) as exc:
        admission.authenticate(f"Bearer {forged}")
    assert exc.value.status_code == 401


def test_admission_revoked_and_expired_keys_are_401() -> None:
    revoked, expired = generate_key("live", "sh"), generate_key("live", "sh")
    admission = _admission(
        _registry_with(
            _entry(revoked, status="revoked"),
            _entry(expired, expires_at=datetime.now(UTC) - timedelta(minutes=1)),
        )
    )
    for key in (revoked, expired):
        with pytest.raises(HTTPException) as exc:
            admission.authenticate(f"Bearer {key.presentation}")
        assert exc.value.status_code == 401


def test_shared_token_acts_as_the_body_tenant() -> None:
    admission = _admission(shared="shared-secret")
    principal = admission.authenticate("Bearer shared-secret")
    assert principal.kind == "shared_token"
    assert principal.tenant_id is None
    assert Scope.BUNDLES_RELOAD in principal.scopes
    assert principal.bind_tenant("tenant-x") == "tenant-x"
    with pytest.raises(HTTPException) as exc:
        principal.bind_tenant(None)
    assert exc.value.status_code == 400


def test_open_engine_only_in_development_without_any_credential_configured() -> None:
    assert (
        Admission(shared_token=None, registry=None, limiter=None, development=True)
        .authenticate(None)
        .kind
        == "shared_token"
    )
    with pytest.raises(HTTPException):
        _admission().authenticate(None)


# ------------------------------------------------------------ rate limit


def test_rate_limiter_is_a_token_bucket_per_key() -> None:
    clock = [0.0]
    limiter = RateLimiter(rate_per_s=10, burst=3, clock=lambda: clock[0])
    assert [limiter.acquire("a") for _ in range(3)] == [None, None, None]
    wait = limiter.acquire("a")
    assert wait is not None and 0 < wait <= 0.1
    assert limiter.acquire("b") is None  # another key has its own bucket
    clock[0] += 0.1
    assert limiter.acquire("a") is None
    clock[0] += 100
    assert limiter.forget_idle(50) == 2
