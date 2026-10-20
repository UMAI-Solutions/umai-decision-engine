"""Tenant-owned bundles (ADR-0019 §2, policy-bundle 0.8): a tenant resolves, lists and publishes
only its own guardrails; unowned bundles belong to the default tenant. Synthetic data only."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from umai_decision_engine.api.app import create_app as create_engine_app
from umai_decision_engine.control_plane.app import create_app as create_control_app
from umai_decision_engine.policy.manifest import BundleManifest, sha256_hex
from umai_decision_engine.policy.sources import DirectorySource
from umai_decision_engine.policy.store import BundleStore
from umai_decision_engine.settings import Settings

CONTROL = "control-token"
SHARED = "shared-operator-token"


def _store(root) -> BundleStore:
    store = BundleStore(DirectorySource(root), public_keys={}, require_signed=False)
    store.reload(force=True)
    return store


def test_same_bundle_id_in_two_tenants_never_mixes(write_bundle) -> None:
    write_bundle(bundle_yaml(mode="ENFORCE"), active=1)
    write_bundle(bundle_yaml(mode="MONITOR"), active=1, tenant="acme")
    store = _store(write_bundle.root)
    assert store.get("gr-test").bundle.mode == "ENFORCE"  # default tenant
    assert store.get("gr-test", tenant_id="acme").bundle.mode == "MONITOR"
    assert store.get("gr-test", tenant_id="globex") is None
    assert store.get("gr-test", 1, tenant_id="globex") is None
    assert store.get("gr-test", tenant_id="acme").tenant_id == "acme"


def test_manifest_owner_must_match_the_directory(write_bundle) -> None:
    content = bundle_yaml()
    manifest = BundleManifest(
        bundle_id="gr-test",
        version=1,
        contract_version="0.4",
        sha256=sha256_hex(content.encode()),
        tenant_id="globex",
    ).model_dump_json(exclude_none=True)
    write_bundle(content, active=1, manifest=manifest, tenant="acme")
    store = _store(write_bundle.root)
    assert store.get("gr-test", tenant_id="acme") is None
    assert any("does not match the owner" in e.error for e in store.state.errors)


def test_invalid_tenant_directory_is_reported(write_bundle) -> None:
    write_bundle(bundle_yaml(), active=1, tenant="Bad_Name")
    store = _store(write_bundle.root)
    assert any("invalid tenant directory" in e.error for e in store.state.errors)


@pytest.fixture
def stack(write_bundle, detector, tmp_path):
    """Policy service and engine over one policy root, with per-tenant API keys."""

    write_bundle(bundle_yaml(), active=1)  # the default tenant's
    keys = tmp_path / "keys"
    common = dict(
        env="test",
        require_signed_bundles=False,
        policy_dir=write_bundle.root,
        key_registry_dir=keys,
        tenant_hmac_keys={"acme": "a2V5", "globex": "a2V5", "default": "a2V5"},
    )
    control = TestClient(
        create_control_app(
            Settings(
                **common,
                control_db_path=tmp_path / "control.sqlite",
                control_api_token=CONTROL,
                actor_assertion="optional",
            ),
            detector=detector,
        )
    )
    control.__enter__()
    control.headers["Authorization"] = f"Bearer {CONTROL}"
    secrets = {}
    for tenant in ("acme", "globex"):
        control.post("/v1/tenants", json={"id": tenant})
        secrets[tenant] = control.post(
            f"/v1/tenants/{tenant}/keys", json={"name": "t", "scopes": ["evaluate", "bundles:read"]}
        ).json()["secret"]
    engine = TestClient(
        create_engine_app(
            Settings(**common, api_token=SHARED, bundle_poll_interval_s=0), detector=detector
        )
    )
    engine.__enter__()
    yield write_bundle, control, engine, secrets
    engine.__exit__(None, None, None)
    control.__exit__(None, None, None)


def _evaluate(engine, secret: str, bundle_id: str = "gr-test"):
    body = {
        "contract_version": "0.4",
        "request_id": "r-1",
        "timestamp": "2026-10-19T09:00:00Z",
        "bundle": {"id": bundle_id},
        "phase": "PRE_LLM",
        "content": {
            "messages": [{"role": "user", "content": "hello"}],
            "focus": "LAST_USER_MESSAGE",
        },
    }
    return engine.post("/v1/evaluate", json=body, headers={"Authorization": f"Bearer {secret}"})


def test_a_tenant_key_cannot_use_or_see_another_tenants_bundle(stack) -> None:
    _write_bundle, _control, engine, secrets = stack
    response = _evaluate(engine, secrets["acme"])  # gr-test belongs to the default tenant
    assert response.status_code == 200
    assert response.json()["errors"][0]["type"] == "CONFIG_MISSING"
    acme = {"Authorization": f"Bearer {secrets['acme']}"}
    assert engine.get("/v1/bundles", headers=acme).json()["bundles"] == []
    assert engine.get("/v1/bundles/gr-test/versions/1", headers=acme).status_code == 404
    spoofed = {**acme, "X-Umai-Tenant": "default"}
    assert engine.get("/v1/bundles", headers=spoofed).status_code == 403


def test_publishing_for_a_tenant_writes_its_own_signed_owner(stack) -> None:
    write_bundle, control, engine, secrets = stack
    # give acme its own copy of the guardrail (the starter-guardrail path of ADR-0033 §7)
    write_bundle(bundle_yaml(mode="MONITOR"), active=1, tenant="acme")
    acme = {"X-Umai-Tenant": "acme"}
    assert control.post("/v1/guardrails/gr-test/draft", json={}, headers=acme).status_code == 201
    published = control.post(
        "/v1/guardrails/gr-test/draft/publish", json={"activate": True}, headers=acme
    )
    assert published.status_code == 200, published.text
    manifest = json.loads(
        (write_bundle.root / "tenants" / "acme" / "gr-test" / "2" / "manifest.json").read_text()
    )
    assert manifest["tenant_id"] == "acme"
    # the default tenant's guardrail is untouched
    assert control.get("/v1/guardrails/gr-test").json()["active_version"] == 1

    engine.post("/v1/bundles/reload", headers={"Authorization": f"Bearer {SHARED}"})
    acme_key = {"Authorization": f"Bearer {secrets['acme']}"}
    listing = engine.get("/v1/bundles", headers=acme_key).json()
    assert listing["tenant_id"] == "acme"
    assert [b["active_version"] for b in listing["bundles"]] == [2]
    assert listing["errors"] == []  # load errors are for the operator only
    evaluated = _evaluate(engine, secrets["acme"]).json()
    assert evaluated["bundle"]["version"] == 2 and evaluated["bundle"]["mode"] == "MONITOR"
    assert "CONFIG_MISSING" not in {e["type"] for e in evaluated.get("errors", [])}
    # globex still resolves nothing
    assert _evaluate(engine, secrets["globex"]).json()["errors"][0]["type"] == "CONFIG_MISSING"


def test_operator_token_reads_a_tenant_by_header(stack) -> None:
    write_bundle, _control, engine, _secrets = stack
    write_bundle(bundle_yaml(mode="MONITOR"), active=1, tenant="acme")
    engine.post("/v1/bundles/reload", headers={"Authorization": f"Bearer {SHARED}"})
    operator = {"Authorization": f"Bearer {SHARED}"}
    assert engine.get("/v1/bundles", headers=operator).json()["tenant_id"] == "default"
    acme = engine.get("/v1/bundles", headers={**operator, "X-Umai-Tenant": "acme"}).json()
    assert acme["tenant_id"] == "acme" and acme["bundles"][0]["id"] == "gr-test"
