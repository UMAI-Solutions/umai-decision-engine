"""Per-tenant API keys end to end (ADR-0023): created in the control plane, published as a
signed snapshot, loaded by the engine, enforced at admission. Synthetic data only."""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from umai_decision_engine.api.app import create_app as create_engine_app
from umai_decision_engine.control_plane.app import create_app as create_control_app
from umai_decision_engine.policy.manifest import BundleManifest, sha256_hex, sign_manifest
from umai_decision_engine.settings import Settings

CONTROL_TOKEN = "control-token"  # synthetic
TCKN = "10000000146"  # synthetic test vector


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


def _request(tenant_id: str | None, text: str = f"TC kimlik {TCKN}") -> dict:
    body = {
        "contract_version": "0.6",
        "request_id": "req-keys",
        "timestamp": "2026-10-18T09:00:00Z",
        "bundle": {"id": "gr-test"},
        "phase": "PRE_LLM",
        "content": {"messages": [{"role": "user", "content": text}], "focus": "LAST_USER_MESSAGE"},
    }
    if tenant_id is not None:
        body["tenant_id"] = tenant_id
    return body


@pytest.fixture
def stack(write_bundle, detector, tmp_path):
    """A signed bundle, a control plane with a signing key, an engine reading its snapshots."""

    private, public = _keypair()
    content = bundle_yaml()
    manifest = BundleManifest(
        bundle_id="gr-test",
        version=1,
        contract_version="0.4",
        engine=">=0.1.0",
        created_at="2026-10-18T00:00:00Z",
        sha256=sha256_hex(content.encode()),
    )
    signed = sign_manifest(manifest, private, "k1")
    write_bundle(content, active=1, manifest=signed.model_dump_json(exclude_none=True))
    key_file = tmp_path / "signing.key"
    key_file.write_text(private, encoding="utf-8")
    registry_dir = tmp_path / "keys"

    control_settings = Settings(
        env="test",
        policy_dir=write_bundle.root,
        control_db_path=tmp_path / "control.sqlite",
        control_api_token=CONTROL_TOKEN,
        signing_key_file=key_file,
        signing_key_id="k1",
        key_registry_dir=registry_dir,
        region="eu",
        bundle_public_keys={"k1": public},
    )
    engine_settings = Settings(
        env="test",
        policy_dir=write_bundle.root,
        bundle_public_keys={"k1": public},
        key_registry_dir=registry_dir,
        key_registry_poll_interval_s=0,
        bundle_poll_interval_s=0,
        rate_limit_rps=0.001,  # refill ~never within a test: the burst is the whole budget
        rate_limit_burst=5,
        l0_threads=1,
    )
    with (
        TestClient(
            create_control_app(control_settings, detector=detector),
            headers={"Authorization": f"Bearer {CONTROL_TOKEN}"},
        ) as control,
        TestClient(create_engine_app(engine_settings, detector=detector)) as engine,
    ):
        yield control, engine


def _reload(engine: TestClient) -> None:
    engine.app.state.registry.reload()


def _mint(control: TestClient, tenant: str, **body) -> tuple[str, str]:
    created = control.post(f"/v1/tenants/{tenant}/keys", json={"name": "sdk", **body})
    assert created.status_code == 201, created.text
    assert created.json()["published"] is True
    return created.json()["secret"], created.json()["key"]["key_id"]


def test_key_lifecycle_create_use_revoke(stack) -> None:
    control, engine = stack
    assert control.post("/v1/tenants", json={"id": "tenant-a"}).status_code == 201
    secret, key_id = _mint(control, "tenant-a")
    assert secret.startswith("umai_live_eu_")
    assert secret not in control.get("/v1/tenants/tenant-a/keys").text  # never stored

    _reload(engine)
    auth = {"Authorization": f"Bearer {secret}"}
    response = engine.post("/v1/evaluate", json=_request(None), headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["tenant_id"] == "tenant-a"  # derived from the credential
    assert response.json()["decision"]["action"] == "BLOCK"

    revoked = control.delete(f"/v1/tenants/tenant-a/keys/{key_id}")
    assert revoked.status_code == 200 and revoked.json()["key"]["status"] == "revoked"
    _reload(engine)
    assert engine.post("/v1/evaluate", json=_request(None), headers=auth).status_code == 401


def test_key_of_tenant_a_cannot_act_as_tenant_b(stack) -> None:
    control, engine = stack
    control.post("/v1/tenants", json={"id": "tenant-a"})
    control.post("/v1/tenants", json={"id": "tenant-b"})
    secret_a, _ = _mint(control, "tenant-a")
    _reload(engine)
    response = engine.post(
        "/v1/evaluate", json=_request("tenant-b"), headers={"Authorization": f"Bearer {secret_a}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "tenant_mismatch"


def test_scopes_are_enforced_per_route(stack) -> None:
    control, engine = stack
    control.post("/v1/tenants", json={"id": "tenant-a"})
    evaluate_only, _ = _mint(control, "tenant-a")
    reader, _ = _mint(control, "tenant-a", scopes=["bundles:read"])
    _reload(engine)
    as_evaluator = {"Authorization": f"Bearer {evaluate_only}"}
    as_reader = {"Authorization": f"Bearer {reader}"}
    assert engine.get("/v1/bundles", headers=as_evaluator).status_code == 403
    assert engine.get("/v1/bundles", headers=as_reader).status_code == 200
    denied = engine.post("/v1/evaluate", json=_request(None), headers=as_reader)
    assert denied.status_code == 403
    assert denied.json()["detail"]["error"] == "insufficient_scope"
    # a control-plane scope cannot be minted for a customer key
    illegal = control.post(
        "/v1/tenants/tenant-a/keys", json={"name": "x", "scopes": ["bundles:reload"]}
    )
    assert illegal.status_code == 422
    assert engine.post("/v1/bundles/reload", headers=as_reader).status_code == 403


def test_rate_limit_answers_429_with_retry_after(stack) -> None:
    control, engine = stack
    control.post("/v1/tenants", json={"id": "tenant-a"})
    secret, _ = _mint(control, "tenant-a")
    _reload(engine)
    auth = {"Authorization": f"Bearer {secret}"}
    statuses = [engine.get("/v1/bundles", headers=auth).status_code for _ in range(8)]
    assert statuses[:5] == [403] * 5  # evaluate-only key: admitted, then refused by scope
    assert 429 in statuses[5:]
    limited = engine.get("/v1/bundles", headers=auth)
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


def test_snapshot_is_signed_and_unsigned_snapshots_are_refused(stack, tmp_path) -> None:
    control, engine = stack
    control.post("/v1/tenants", json={"id": "tenant-a"})
    _mint(control, "tenant-a")
    snapshot = (tmp_path / "keys" / "tenant-a.json").read_text(encoding="utf-8")
    assert '"signature"' in snapshot and '"signing_key_id": "k1"' in snapshot
    # strip the signature: the engine keeps the last good registry and reports the file
    (tmp_path / "keys" / "tenant-a.json").write_text(
        snapshot.replace('"signature"', '"signature_removed"'), encoding="utf-8"
    )
    _reload(engine)
    assert engine.app.state.registry.state.errors


def test_unknown_tenant_and_missing_key_are_404(stack) -> None:
    control, _ = stack
    assert control.post("/v1/tenants/nobody/keys", json={"name": "x"}).status_code == 404
    control.post("/v1/tenants", json={"id": "tenant-a"})
    assert control.delete("/v1/tenants/tenant-a/keys/ZZZZZZZZ").status_code == 404
    assert control.post("/v1/tenants", json={"id": "tenant-a"}).status_code == 409
