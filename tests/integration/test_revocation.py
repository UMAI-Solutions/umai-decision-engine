"""Revocation latency (launch plan S0-2: a revoked key is refused within 10 s; ADR-0023 §6):
the policy service revokes, the engine API and the gateway refuse the key after their own
background registry poll, with nobody calling reload. The end-to-end measurement on separate
processes is the `revocation` eval suite. Synthetic data only."""

from __future__ import annotations

import base64
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from umai_decision_engine.api.app import create_app as create_engine_app
from umai_decision_engine.control_plane.app import create_app as create_control_app
from umai_decision_engine.gateway.app import create_gateway_app
from umai_decision_engine.settings import Settings

CONTROL_TOKEN = "control-token"  # synthetic
BUDGET_S = 10.0  # launch plan S0-2
POLL_S = 0.2


def _keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode(), base64.b64encode(public).decode()


def _request() -> dict:
    return {
        "contract_version": "0.12",
        "request_id": "req-revocation",
        "timestamp": "2026-10-31T09:00:00Z",
        "bundle": {"id": "gr-test"},
        "phase": "PRE_LLM",
        "content": {
            "messages": [{"role": "user", "content": "Toplantı notlarını özetler misin?"}],
            "focus": "LAST_USER_MESSAGE",
        },
    }


@pytest.fixture
def stack(write_bundle, detector, tmp_path):
    """Policy service, engine API and gateway sharing one key-registry directory, the two
    readers polling it in the background as deployed (only faster)."""

    write_bundle(bundle_yaml(), active=1)
    private, public = _keypair()
    key_file = tmp_path / "signing.key"
    key_file.write_text(private, encoding="utf-8")
    common = {
        "env": "test",
        "policy_dir": write_bundle.root,
        "key_registry_dir": tmp_path / "keys",
        "applications_dir": tmp_path / "applications",
        "bundle_public_keys": {"k1": public},
        "require_signed_bundles": False,
        "l0_threads": 1,
        "decision_warm_up_timeout_ms": 0,
    }
    control = Settings(
        **common,
        control_db_path=tmp_path / "control.sqlite",
        control_api_token=CONTROL_TOKEN,
        signing_key_file=key_file,
        signing_key_id="k1",
    )
    reader = Settings(**common, key_registry_poll_interval_s=POLL_S, bundle_poll_interval_s=0)
    with (
        TestClient(
            create_control_app(control, detector=detector),
            headers={"Authorization": f"Bearer {CONTROL_TOKEN}"},
        ) as policy,
        TestClient(create_engine_app(reader, detector=detector)) as engine,
        TestClient(create_gateway_app(reader, detector=detector)) as gateway,
    ):
        yield policy, engine, gateway


def _status(client: TestClient, secret: str) -> int:
    auth = {"Authorization": f"Bearer {secret}"}
    if "gateway" in client.app.title.lower():  # no application: 404 once admitted, 401 if not
        body = {"model": "m", "messages": [{"role": "user", "content": "Merhaba"}]}
        return client.post("/v1/chat/completions", json=body, headers=auth).status_code
    return client.post("/v1/evaluate", json=_request(), headers=auth).status_code


def _seconds_until(client: TestClient, secret: str, *, refused: bool, limit_s: float) -> float:
    started = time.monotonic()
    while (elapsed := time.monotonic() - started) < limit_s:
        if (_status(client, secret) == 401) == refused:
            return elapsed
        time.sleep(0.02)
    raise AssertionError(f"no change within {limit_s} s")


def test_revoked_key_is_refused_after_one_background_poll(stack) -> None:
    policy, engine, gateway = stack
    assert policy.post("/v1/tenants", json={"id": "tenant-a"}).status_code == 201
    created = policy.post(
        "/v1/tenants/tenant-a/keys", json={"name": "sdk", "scopes": ["evaluate", "gateway"]}
    )
    assert created.status_code == 201 and created.json()["published"] is True
    secret, key_id = created.json()["secret"], created.json()["key"]["key_id"]
    for reader in (engine, gateway):  # a new key works after one poll too
        _seconds_until(reader, secret, refused=False, limit_s=5 * POLL_S + 2)

    revoked = policy.delete(f"/v1/tenants/tenant-a/keys/{key_id}")
    assert revoked.status_code == 200 and revoked.json()["published"] is True
    for reader in (engine, gateway):  # one interval plus the reload; generous for a busy runner
        _seconds_until(reader, secret, refused=True, limit_s=5 * POLL_S + 2)


def test_default_poll_interval_leaves_room_in_the_revocation_budget() -> None:
    """Deployments that do not set the interval (the single VM does not) must revoke within
    S0-2's 10 s: one interval, the reload, and margin for a busy process."""

    interval = Settings(_env_file=None).key_registry_poll_interval_s  # type: ignore[call-arg]
    assert 0 < interval <= BUDGET_S / 2
