"""Key network restrictions end to end (ADR-0046): allowed networks set and edited in the policy
service, published in the signed snapshot, enforced by the engine API and the gateway; the
client address behind a trusted proxy; the failed-credential throttle. Documentation addresses
only (RFC 5737, RFC 3849); synthetic keys."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from tests.integration.test_api_keys import CONTROL_TOKEN, _keypair, _request
from tests.integration.test_gateway import (  # noqa: F401  (fixtures)
    make_gateway,
    upstream,
)
from umai_decision_engine.api.app import create_app as create_engine_app
from umai_decision_engine.contracts.credentials import (
    KeyRegistryEntry,
    KeyRegistrySnapshot,
    Scope,
)
from umai_decision_engine.control_plane.app import create_app as create_control_app
from umai_decision_engine.policy.manifest import BundleManifest, sha256_hex, sign_manifest
from umai_decision_engine.settings import Settings
from umai_decision_engine.tenancy.keys import generate_key

OFFICE = "203.0.113.10"  # inside 203.0.113.0/24, the allowed network
ELSEWHERE = "198.51.100.7"
PROXY = "192.0.2.10"


@pytest.fixture
def stack(write_bundle, detector, tmp_path):
    """A control plane that signs snapshots and a factory for engines reading them, each engine
    seen from a chosen peer address."""

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
    entered: list[TestClient] = []

    def engine(**settings: Any) -> Any:
        cfg = Settings(
            env="test",
            policy_dir=write_bundle.root,
            bundle_public_keys={"k1": public},
            key_registry_dir=registry_dir,
            key_registry_poll_interval_s=0,
            bundle_poll_interval_s=0,
            l0_threads=1,
            **settings,
        )
        app = create_engine_app(cfg, detector=detector)
        entered.append(TestClient(app).__enter__())  # runs the lifespan once

        def seen_from(address: str) -> TestClient:
            app.state.registry.reload()
            return TestClient(app, client=(address, 50000))

        return seen_from

    with TestClient(
        create_control_app(control_settings, detector=detector),
        headers={"Authorization": f"Bearer {CONTROL_TOKEN}"},
    ) as control:
        control.post("/v1/tenants", json={"id": "tenant-a"})
        yield control, engine, registry_dir
    for client in entered:
        client.__exit__(None, None, None)


def _mint(control: TestClient, **body: Any) -> tuple[str, dict[str, Any]]:
    created = control.post("/v1/tenants/tenant-a/keys", json={"name": "sdk", **body})
    assert created.status_code == 201, created.text
    assert created.json()["published"] is True
    return created.json()["secret"], created.json()["key"]


def _evaluate(client: TestClient, secret: str, **headers: str) -> Any:
    return client.post(
        "/v1/evaluate",
        json=_request(None),
        headers={"Authorization": f"Bearer {secret}", **headers},
    )


def test_networks_are_set_at_creation_edited_and_cleared(stack) -> None:
    control, engine, registry_dir = stack
    secret, key = _mint(control, allowed_ips=["203.0.113.0/24", "2001:DB8::/32"])
    assert key["allowed_ips"] == ["203.0.113.0/24", "2001:db8::/32"]  # canonical form
    listed = control.get("/v1/tenants/tenant-a/keys").json()["keys"]
    assert listed[0]["allowed_ips"] == ["203.0.113.0/24", "2001:db8::/32"]
    snapshot = json.loads((registry_dir / "tenant-a.json").read_text(encoding="utf-8"))
    assert snapshot["contract_version"] == "0.5"
    assert snapshot["keys"][0]["allowed_ips"] == ["203.0.113.0/24", "2001:db8::/32"]

    seen_from = engine()
    assert _evaluate(seen_from(OFFICE), secret).status_code == 200
    assert _evaluate(seen_from("2001:db8:7::1"), secret).status_code == 200
    refused = _evaluate(seen_from(ELSEWHERE), secret)
    assert refused.status_code == 403
    assert refused.json() == {
        "detail": {
            "error": "ip_not_allowed",
            "detail": "this key may not be used from this address",
        }
    }

    moved = control.patch(
        f"/v1/tenants/tenant-a/keys/{key['key_id']}", json={"allowed_ips": ["198.51.100.0/24"]}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["published"] is True
    assert moved.json()["key"]["allowed_ips"] == ["198.51.100.0/24"]
    assert _evaluate(seen_from(ELSEWHERE), secret).status_code == 200  # after one reload
    assert _evaluate(seen_from(OFFICE), secret).status_code == 403

    cleared = control.patch(f"/v1/tenants/tenant-a/keys/{key['key_id']}", json={"allowed_ips": []})
    assert cleared.status_code == 200 and "allowed_ips" not in cleared.json()["key"]
    assert _evaluate(seen_from(OFFICE), secret).status_code == 200
    assert _evaluate(seen_from(ELSEWHERE), secret).status_code == 200


def test_wrong_networks_get_a_message_and_revoked_keys_stay_as_they_are(stack) -> None:
    control, _, _ = stack
    wrong = control.post(
        "/v1/tenants/tenant-a/keys", json={"name": "x", "allowed_ips": ["203.0.113.5/24"]}
    )
    assert wrong.status_code == 422
    assert wrong.json()["detail"] == {
        "error": "invalid_allowed_ips",
        "detail": "entry 1 has host bits set: write 203.0.113.0/24 for the whole network, "
        "or the address alone for one host",
    }
    assert control.get("/v1/tenants/tenant-a/keys").json()["keys"] == []  # nothing created

    _, key = _mint(control)
    path = f"/v1/tenants/tenant-a/keys/{key['key_id']}"
    garbage = control.patch(path, json={"allowed_ips": ["198.51.100.0/24", "office"]})
    assert garbage.status_code == 422
    assert garbage.json()["detail"]["detail"] == "entry 2 is not an IP address or CIDR network"
    assert control.patch(path, json={"allowed_ip": ["198.51.100.0/24"]}).status_code == 422
    missing = control.patch("/v1/tenants/tenant-a/keys/ZZZZZZZZ", json={"allowed_ips": None})
    assert missing.status_code == 404
    control.delete(path)
    revoked = control.patch(path, json={"allowed_ips": ["198.51.100.0/24"]})
    assert revoked.status_code == 409
    assert revoked.json()["detail"]["error"] == "key_revoked"


def test_only_a_trusted_proxy_names_the_client(stack) -> None:
    control, engine, _ = stack
    secret, _ = _mint(control, allowed_ips=["203.0.113.0/24"])
    behind_caddy = engine(trusted_proxies=[f"{PROXY}/32"])
    via_proxy = behind_caddy(PROXY)
    # The proxy appended the client it saw; what the client claimed to the left is ignored.
    assert _evaluate(via_proxy, secret, **{"X-Forwarded-For": OFFICE}).status_code == 200
    assert (
        _evaluate(via_proxy, secret, **{"X-Forwarded-For": f"{OFFICE}, {ELSEWHERE}"}).status_code
        == 403
    )
    # a direct caller cannot claim an address, behind a trusted proxy or without one
    direct = behind_caddy(ELSEWHERE)
    assert _evaluate(direct, secret, **{"X-Forwarded-For": OFFICE}).status_code == 403
    no_proxies = engine()(PROXY)
    assert _evaluate(no_proxies, secret, **{"X-Forwarded-For": OFFICE}).status_code == 403


def test_a_source_with_too_many_bad_credentials_gets_429(stack, caplog) -> None:
    control, engine, _ = stack
    secret, _ = _mint(control)
    seen_from = engine()  # defaults: 20 failures per 60 s
    attacker, neighbour = seen_from(ELSEWHERE), seen_from(OFFICE)
    forged = secret[:-6] + "AAAAAA"
    caplog.set_level(logging.INFO)
    statuses = [_evaluate(attacker, forged).status_code for _ in range(20)]
    assert statuses == [401] * 20
    throttled = _evaluate(attacker, forged)
    assert throttled.status_code == 429
    assert throttled.json()["detail"]["error"] == "too_many_failed_attempts"
    assert throttled.headers["Retry-After"] == "3"  # one attempt back after 60 s / 20
    assert _evaluate(attacker, secret).status_code == 429  # refused before the lookup
    assert _evaluate(neighbour, secret).status_code == 200
    assert "admission.throttled source=198.51.100.7" in caplog.text
    assert forged not in caplog.text and secret not in caplog.text
    assert secret.split("_")[-1] not in caplog.text


# ------------------------------------------------------------------ gateway


def _gateway_registry(tmp_path, key, allowed_ips: list[str] | None) -> Any:
    directory = tmp_path / "keys"
    directory.mkdir(exist_ok=True)
    snapshot = KeyRegistrySnapshot(
        tenant_id="tenant-a",
        generated_at=datetime.now(UTC),
        keys=[
            KeyRegistryEntry(
                key_id=key.key_id,
                secret_sha256=key.secret_sha256,
                env="live",
                region="sh",
                scopes=[Scope.GATEWAY, Scope.EVALUATE],
                status="active",
                allowed_ips=allowed_ips,
            )
        ],
    )
    (directory / "tenant-a.json").write_text(snapshot.model_dump_json(), encoding="utf-8")
    return directory


def _chat(client: TestClient, secret: str) -> Any:
    return client.post(
        "/v1/chat/completions",
        json={"model": "gpt-eu", "messages": [{"role": "user", "content": "selam"}]},
        headers={"Authorization": f"Bearer {secret}"},
    )


def _from(gateway: TestClient, address: str) -> TestClient:
    return TestClient(
        gateway.app, client=(address, 50000), headers={"X-Umai-Provider-Key": "sk-test"}
    )


def test_gateway_refuses_other_networks(make_gateway, upstream, tmp_path) -> None:  # noqa: F811
    """A key outside its networks is refused before anything is checked or forwarded."""
    key = generate_key("live", "sh")
    gateway = make_gateway(
        env="test",
        key_registry_dir=_gateway_registry(tmp_path, key, ["203.0.113.0/24"]),
        require_signed_bundles=False,
    )
    refused = _chat(_from(gateway, ELSEWHERE), key.presentation)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "ip_not_allowed"
    assert refused.json()["error"]["type"] == "umai_gateway"
    assert upstream.requests == []  # nothing was checked or forwarded
    assert _chat(_from(gateway, OFFICE), key.presentation).status_code == 200
    assert len(upstream.requests) == 1


def test_gateway_throttles_bad_credentials(make_gateway, upstream, tmp_path) -> None:  # noqa: F811
    key = generate_key("live", "sh")
    gateway = make_gateway(
        env="test",
        key_registry_dir=_gateway_registry(tmp_path, key, None),
        require_signed_bundles=False,
        auth_failure_limit=2,
        auth_failure_window_s=30,
    )
    attacker = _from(gateway, "2001:db8:0:1::7")
    forged = key.presentation[:-6] + "AAAAAA"
    assert [_chat(attacker, forged).status_code for _ in range(2)] == [401, 401]
    rotated = _from(gateway, "2001:db8:0:1::8")  # same /64
    throttled = _chat(rotated, key.presentation)
    assert throttled.status_code == 429
    assert throttled.json()["error"]["code"] == "too_many_failed_attempts"
    assert throttled.headers["Retry-After"] == "15"
    assert _chat(_from(gateway, "2001:db8:0:2::7"), key.presentation).status_code == 200
    assert len(upstream.requests) == 1
