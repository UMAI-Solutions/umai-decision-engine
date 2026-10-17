"""Policy service (control plane, ADR-0022): drafts, validation, golden tests, publish with
accepted changes, activation and rollback, signing, auth. Synthetic data only."""

from __future__ import annotations

import base64
import json
import textwrap

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from umai_decision_engine.control_plane.app import create_app
from umai_decision_engine.policy.manifest import BundleManifest, verify_manifest
from umai_decision_engine.policy.sources import DirectorySource
from umai_decision_engine.policy.store import BundleStore
from umai_decision_engine.settings import Settings

MASK_PHONE = """
on_detect:
  default: BLOCK
  actions: { TR_PHONE: ALLOW_WITH_MODIFICATIONS }
"""

CASES = """
contract_version: "0.2"
cases:
  - id: tckn-blocked
    input: { text: "TC kimlik 10000000146" }
    expect: { action: BLOCK, entities: [TR_TCKN] }
  - id: phone-masked
    input: { text: "cep 0532 123 45 67" }
    expect:
      action: ALLOW_WITH_MODIFICATIONS
      masked: [TR_PHONE]
      redacted_text: "cep <TR_PHONE_1>"
"""


@pytest.fixture
def service(write_bundle, detector, tmp_path):
    directory = write_bundle(bundle_yaml(l0_extra=MASK_PHONE), active=1)
    (directory / "tests").mkdir()
    (directory / "tests" / "cases.yaml").write_bytes(textwrap.dedent(CASES).encode("utf-8"))
    clients = []

    def _make(**overrides):
        settings = Settings(
            env=overrides.pop("env", "development"),
            policy_dir=write_bundle.root,
            control_db_path=tmp_path / "control.sqlite",
            **overrides,
        )
        client = TestClient(create_app(settings, detector=detector))
        client.__enter__()
        clients.append(client)
        return client

    _make.root = write_bundle.root
    yield _make
    for client in clients:
        client.__exit__(None, None, None)


def _policy(draft: dict, policy_id: str) -> dict:
    return next(p for p in draft["bundle"]["l0"]["policies"] if p["id"] == policy_id)


def test_list_summary_and_catalog(service) -> None:
    client = service()
    assert client.get("/v1/guardrails").json()["guardrails"] == [
        {
            "id": "gr-test",
            "name": None,
            "description": None,
            "mode": "ENFORCE",
            "active_version": 1,
            "versions": [1],
            "has_draft": False,
            "policies": 0,
            "policies_on": 0,
            "detectors": 6,
            "last_change": None,
        }
    ]
    summary = client.get("/v1/guardrails/gr-test").json()
    assert summary["versions"][0]["valid"] and summary["draft"] is None
    groups = {e["type"]: e["group"] for e in client.get("/v1/catalog").json()["entities"]}
    assert groups["TR_TCKN"] == "Türkiye" and groups["API_KEY"] == "Secrets"
    assert groups["ES_NIF"] == "Europe" and groups["IP_ADDRESS"] == "Global"
    assert client.get("/v1/guardrails/nope").status_code == 404


def test_draft_from_legacy_bundle_is_in_policy_form(service) -> None:
    client = service()
    draft = client.post("/v1/guardrails/gr-test/draft", json={}).json()
    assert draft["base_version"] == 1 and draft["problems"] == []
    policies = {p["id"]: p for p in draft["bundle"]["l0"]["policies"]}
    assert policies["masked-data"]["entities"] == ["TR_PHONE"]
    assert "TR_TCKN" in policies["blocked-data"]["entities"]
    assert client.post("/v1/guardrails/gr-test/draft", json={}).status_code == 409
    assert client.get("/v1/guardrails/gr-test").json()["draft"]["base_version"] == 1


def test_invalid_draft_is_saved_but_cannot_publish(service) -> None:
    client = service()
    draft = client.post("/v1/guardrails/gr-test/draft", json={}).json()
    _policy(draft, "masked-data")["entities"].append("TR_TCKN")  # now in two policies
    saved = client.put("/v1/guardrails/gr-test/draft", json={"bundle": draft["bundle"]}).json()
    assert any("already in policy" in p for p in saved["problems"])
    response = client.post("/v1/guardrails/gr-test/draft/publish", json={"activate": True})
    assert response.status_code == 422 and response.json()["details"]["problems"]


def test_switching_a_policy_off_needs_accepted_cases_then_publishes(service) -> None:
    client = service()
    draft = client.post("/v1/guardrails/gr-test/draft", json={}).json()
    _policy(draft, "masked-data")["enabled"] = False
    client.put("/v1/guardrails/gr-test/draft", json={"bundle": draft["bundle"]})

    report = client.post("/v1/guardrails/gr-test/draft/test").json()
    failed = [c for c in report["cases"] if not c["passed"]]
    assert report["total"] == 2 and [c["case_id"] for c in failed] == ["phone-masked"]
    assert failed[0]["actual"]["action"] == "ALLOW" and failed[0]["actual"]["entities"] == []

    refused = client.post("/v1/guardrails/gr-test/draft/publish", json={"activate": True})
    assert refused.status_code == 422
    assert refused.json()["details"]["unaccepted"] == ["phone-masked"]

    published = client.post(
        "/v1/guardrails/gr-test/draft/publish",
        json={"activate": True, "accept": ["phone-masked"], "note": "phones no longer masked"},
    ).json()
    assert published["version"] == 2 and published["activated"] is True
    assert published["accepted"] == ["phone-masked"]

    root = service.root / "gr-test"
    assert (root / "active").read_text().strip() == "2"
    cases = yaml.safe_load((root / "2" / "tests" / "cases.yaml").read_text(encoding="utf-8"))
    phone = next(c for c in cases["cases"] if c["id"] == "phone-masked")
    assert phone["expect"] == {"action": "ALLOW", "masked": []}  # rewritten from the decision
    assert (root / "2" / "manifest.json").is_file()
    actions = [
        e["action"] for e in map(json.loads, (root / "changelog.jsonl").read_text().splitlines())
    ]
    assert actions == ["publish", "activate"]
    assert client.get("/v1/guardrails/gr-test/draft").status_code == 404  # draft consumed
    v2_summary = client.get("/v1/guardrails/gr-test").json()["versions"][1]
    assert v2_summary["note"] == "phones no longer masked" and v2_summary["base_version"] == 1
    assert v2_summary["published_at"] and v2_summary["signed"] is False  # no signing key set

    store = BundleStore(DirectorySource(service.root), public_keys={}, require_signed=False)
    store.reload(force=True)
    v2 = store.get("gr-test")
    assert v2.bundle.version == 2 and v2.bundle.contract_version == "0.7"
    assert [p.enabled for p in v2.bundle.l0.policies if p.id == "masked-data"] == [False]

    # the new version's golden cases pass as written
    assert client.post("/v1/guardrails/gr-test/draft", json={}).json()["base_version"] == 2
    assert client.post("/v1/guardrails/gr-test/draft/test").json()["passed"] is True


def test_publish_only_and_rollback(service) -> None:
    client = service()
    draft = client.post("/v1/guardrails/gr-test/draft", json={}).json()
    draft["bundle"]["name"] = "Test guardrail"
    client.put("/v1/guardrails/gr-test/draft", json={"bundle": draft["bundle"]})
    published = client.post("/v1/guardrails/gr-test/draft/publish", json={"activate": False}).json()
    assert published == {
        "version": 2,
        "activated": False,
        "previous_active": None,
        "accepted": [],
        "tests": {"total": 2},
    }
    assert client.get("/v1/guardrails/gr-test").json()["active_version"] == 1
    assert client.post("/v1/guardrails/gr-test/activate", json={"version": 2}).json() == {
        "active_version": 2,
        "previous_active": 1,
    }
    assert client.post("/v1/guardrails/gr-test/activate", json={"version": 1}).json() == {
        "active_version": 1,
        "previous_active": 2,
    }
    assert client.post("/v1/guardrails/gr-test/activate", json={"version": 9}).status_code == 422


def test_discard_draft(service) -> None:
    client = service()
    client.post("/v1/guardrails/gr-test/draft", json={})
    assert client.delete("/v1/guardrails/gr-test/draft").status_code == 204
    assert client.delete("/v1/guardrails/gr-test/draft").status_code == 404


def test_published_versions_are_signed_with_a_configured_key(service, tmp_path) -> None:
    key = Ed25519PrivateKey.generate()
    private = base64.b64encode(
        key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    ).decode()
    public = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    key_file = tmp_path / "signing.key"
    key_file.write_text(private)
    client = service(signing_key_file=key_file, signing_key_id="test-key")
    client.post("/v1/guardrails/gr-test/draft", json={})
    client.post("/v1/guardrails/gr-test/draft/publish", json={})
    version_dir = service.root / "gr-test" / "2"
    manifest = BundleManifest.model_validate_json((version_dir / "manifest.json").read_bytes())
    assert verify_manifest(
        manifest,
        (version_dir / "bundle.yaml").read_bytes(),
        {"test-key": public},
        require_signature=True,
    )
    assert client.get("/v1/guardrails/gr-test").json()["versions"][1]["signed"] is True


def test_token_required_when_configured(service) -> None:
    client = service(control_api_token="control-token")
    assert client.get("/v1/guardrails").status_code == 401
    ok = client.get("/v1/guardrails", headers={"Authorization": "Bearer control-token"})
    assert ok.status_code == 200
    assert client.get("/healthz").status_code == 200


def test_refuses_to_start_without_token_outside_development(service) -> None:
    with pytest.raises(RuntimeError, match="UMAI_DE_CONTROL_API_TOKEN"):
        service(env="test")
