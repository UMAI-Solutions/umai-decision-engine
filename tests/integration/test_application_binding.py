"""One guardrail per application on every path: on the evaluation API a key that belongs to an
application is evaluated with the application's guardrail (evaluation API 0.10, ADR-0039 §2).
Synthetic data only."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from umai_decision_engine.api.app import create_app
from umai_decision_engine.contracts.credentials import (
    KeyRegistryEntry,
    KeyRegistrySnapshot,
    Scope,
)
from umai_decision_engine.settings import Settings
from umai_decision_engine.tenancy.keys import generate_key

TCKN = "10000000146"


def _request(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "contract_version": "0.10",
        "request_id": "req-app",
        "timestamp": "2026-10-20T09:00:00Z",
        "phase": "PRE_LLM",
        "content": {
            "messages": [{"role": "user", "content": f"TC kimlik {TCKN}"}],
            "focus": "LAST_USER_MESSAGE",
        },
    }
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


@pytest.fixture
def engine(write_bundle, detector, tmp_path):
    write_bundle(bundle_yaml("gr-test"), "gr-test", active=1, tenant="tenant-a")
    write_bundle(bundle_yaml("gr-other"), "gr-other", active=1, tenant="tenant-a")
    applications = tmp_path / "applications"
    applications.mkdir()
    (applications / "tenant-a.yaml").write_text(
        json.dumps({"applications": [{"application_id": "hr-bot", "guardrail": "gr-test"}]}),
        encoding="utf-8",
    )
    bound, unbound, orphan = (generate_key("live", "sh") for _ in range(3))
    keys = tmp_path / "keys"
    keys.mkdir()
    snapshot = KeyRegistrySnapshot(
        tenant_id="tenant-a",
        generated_at=datetime.now(UTC),
        keys=[
            KeyRegistryEntry(
                key_id=k.key_id,
                secret_sha256=k.secret_sha256,
                env="live",
                region="sh",
                scopes=[Scope.EVALUATE],
                application_id=application_id,
                status="active",
            )
            for k, application_id in ((bound, "hr-bot"), (unbound, None), (orphan, "gone"))
        ],
    )
    (keys / "tenant-a.json").write_text(snapshot.model_dump_json(), encoding="utf-8")
    settings = Settings(
        env="test",
        policy_dir=write_bundle.root,
        require_signed_bundles=False,
        key_registry_dir=keys,
        key_registry_poll_interval_s=0,
        bundle_poll_interval_s=0,
        applications_dir=applications,
        applications_poll_interval_s=0,
        l0_threads=1,
    )
    with TestClient(create_app(settings, detector=detector)) as client:
        yield client, {"bound": bound, "unbound": unbound, "orphan": orphan}


def _evaluate(client: TestClient, key, body: dict[str, Any]):
    return client.post(
        "/v1/evaluate", json=body, headers={"Authorization": f"Bearer {key.presentation}"}
    )


def test_an_applications_key_needs_no_guardrail_in_the_request(engine) -> None:
    client, keys = engine
    response = _evaluate(client, keys["bound"], _request())
    assert response.status_code == 200, response.text
    assert response.json()["bundle"]["id"] == "gr-test"
    same = _evaluate(client, keys["bound"], _request(bundle={"id": "gr-test"}))
    assert same.status_code == 200


def test_an_applications_key_cannot_name_another_guardrail(engine) -> None:
    client, keys = engine
    response = _evaluate(client, keys["bound"], _request(bundle={"id": "gr-other"}))
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "guardrail_mismatch"


def test_other_keys_name_the_guardrail_as_before(engine) -> None:
    client, keys = engine
    missing = _evaluate(client, keys["unbound"], _request())
    assert missing.status_code == 422
    assert missing.json()["detail"]["error"] == "bundle_required"
    named = _evaluate(client, keys["unbound"], _request(bundle={"id": "gr-other"}))
    assert named.status_code == 200
    assert named.json()["bundle"]["id"] == "gr-other"


def test_a_key_of_a_missing_application_is_refused(engine) -> None:
    client, keys = engine
    response = _evaluate(client, keys["orphan"], _request(bundle={"id": "gr-test"}))
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "unknown_application"


def test_a_signed_0_2_snapshot_still_verifies_after_the_rename() -> None:
    """Snapshots published by credentials 0.5 call the binding gateway_id (umai-beta has them)."""

    private = Ed25519PrivateKey.generate()
    old = {
        "contract_version": "0.2",
        "tenant_id": "smarttech",
        "generated_at": "2026-10-20T07:15:00Z",
        "keys": [
            {
                "key_id": "AbCdEf12",
                "secret_sha256": "a" * 64,
                "env": "live",
                "region": "eu",
                "scopes": ["gateway"],
                "gateway_id": "main",
                "status": "active",
            },
        ],
        "signing_key_id": "k1",
    }
    signed = json.dumps(old, separators=(",", ":"), ensure_ascii=False).encode()
    old["signature"] = base64.b64encode(private.sign(signed)).decode()
    parsed = KeyRegistrySnapshot.model_validate_json(json.dumps(old))
    assert parsed.keys[0].application_id == "main"
    private.public_key().verify(base64.b64decode(parsed.signature), parsed.signing_payload())
