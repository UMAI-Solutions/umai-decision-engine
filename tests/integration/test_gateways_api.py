"""Gateways managed through the policy service and keys bound to them (gateway-api 0.9,
credentials 0.5, ADR-0038). Synthetic data only."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from umai_decision_engine.control_plane.app import create_app
from umai_decision_engine.gateway.gateways import GatewayTable
from umai_decision_engine.gatewayfiles import AllowList
from umai_decision_engine.settings import Settings

GATEWAY: dict[str, Any] = {"provider": {"kind": "openai"}, "guardrail": "gr-test"}


@pytest.fixture
def control(write_bundle, detector, tmp_path):
    write_bundle(bundle_yaml(), active=1)  # owned by the default tenant
    gateways_dir = tmp_path / "gateways"
    settings = Settings(
        env="development",
        policy_dir=write_bundle.root,
        control_db_path=tmp_path / "control.sqlite",
        gateways_dir=gateways_dir,
        key_registry_dir=tmp_path / "keys",
    )
    client = TestClient(create_app(settings, detector=detector))
    client.__enter__()
    client.gateways_dir = gateways_dir  # type: ignore[attr-defined]
    client.settings = settings  # type: ignore[attr-defined]
    yield client
    client.__exit__(None, None, None)


def _table(control) -> GatewayTable:
    table = GatewayTable(
        control.gateways_dir, AllowList(control.settings.gateway_upstream_allowlist)
    )
    table.reload(force=True)
    return table


def test_gateways_round_trip_and_reach_the_gateway(control) -> None:
    assert control.get("/v1/gateways").json() == {"tenant_id": "default", "gateways": []}
    created = control.put("/v1/gateways/hr-assistant", json={**GATEWAY, "name": "HR assistant"})
    assert created.status_code == 201
    stored = created.json()["gateway"]
    assert stored["gateway_id"] == "hr-assistant"
    assert stored["provider"]["base_url"] == "https://api.openai.com/v1"  # the default
    changed = control.put("/v1/gateways/hr-assistant", json={**GATEWAY, "models": ["gpt-4.1"]})
    assert changed.status_code == 200
    listed = control.get("/v1/gateways").json()["gateways"]
    assert [g["gateway_id"] for g in listed] == ["hr-assistant"]
    assert listed[0]["models"] == ["gpt-4.1"]

    written = yaml.safe_load((control.gateways_dir / "default.yaml").read_text(encoding="utf-8"))
    assert written["gateways"][0]["guardrail"] == "gr-test"
    table = _table(control)
    assert table.get("default", "hr-assistant") is not None and table.errors == []

    assert control.delete("/v1/gateways/hr-assistant").json()["deleted"] == "hr-assistant"
    assert _table(control).get("default", "hr-assistant") is None
    assert control.delete("/v1/gateways/hr-assistant").status_code == 404


@pytest.mark.parametrize(
    ("body", "error", "fragment"),
    [
        ({**GATEWAY, "provider": {"credential": "secret"}}, "invalid_gateway", "secret"),
        (
            {**GATEWAY, "provider": {"kind": "openai", "base_url": "https://10.0.0.5/v1"}},
            "invalid_gateway",
            "allow-list",
        ),
        ({**GATEWAY, "provider": {"kind": "azure_openai"}}, "invalid_gateway", "base_url"),
        ({**GATEWAY, "gateway_id": "another"}, "invalid_gateway", "path"),
        ({**GATEWAY, "fallback": "missing"}, "invalid_gateway", "fallback"),
        ({**GATEWAY, "models": ["bad name"]}, "invalid_gateway", "model name"),
        ({**GATEWAY, "guardrail": "gr-elsewhere"}, "unknown_guardrail", "gr-elsewhere"),
    ],
)
def test_invalid_gateways_are_refused_and_nothing_is_written(control, body, error, fragment):
    response = control.put("/v1/gateways/hr-assistant", json=body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error"] == error and fragment in detail["detail"]
    assert not (control.gateways_dir / "default.yaml").exists()


def test_body_must_be_a_gateway_object(control) -> None:
    assert control.put("/v1/gateways/hr-assistant", content=b"not json").status_code == 422
    assert control.put("/v1/gateways/Bad_Id", json=GATEWAY).status_code == 422


def test_keys_bind_to_a_gateway_that_then_cannot_be_deleted(control) -> None:
    control.put("/v1/gateways/hr-assistant", json=GATEWAY)
    created = control.post(
        "/v1/tenants/default/keys", json={"name": "hr-bot", "gateway_id": "hr-assistant"}
    )
    assert created.status_code == 201, created.text
    key = created.json()["key"]
    assert key["gateway_id"] == "hr-assistant" and key["scopes"] == ["gateway"]
    listed = control.get("/v1/tenants/default/keys").json()["keys"]
    assert listed[0]["gateway_id"] == "hr-assistant"
    snapshot = (control.settings.key_registry_dir / "default.json").read_text(encoding="utf-8")
    assert '"gateway_id": "hr-assistant"' in snapshot and '"contract_version": "0.2"' in snapshot

    refused = control.delete("/v1/gateways/hr-assistant")
    assert refused.status_code == 409
    assert refused.json()["detail"]["error"] == "gateway_in_use"
    control.delete(f"/v1/tenants/default/keys/{key['key_id']}")
    assert control.delete("/v1/gateways/hr-assistant").status_code == 200


@pytest.mark.parametrize(
    ("body", "status", "fragment"),
    [
        ({"name": "x", "gateway_id": "missing"}, 422, "unknown_gateway"),
        ({"name": "x", "gateway_id": "hr-assistant", "scopes": ["evaluate"]}, 422, "gateway"),
    ],
)
def test_binding_needs_an_existing_gateway_and_the_scope(control, body, status, fragment):
    control.put("/v1/gateways/hr-assistant", json=GATEWAY)
    response = control.post("/v1/tenants/default/keys", json=body)
    assert response.status_code == status
    assert fragment in response.text


def test_a_gateway_others_fall_back_to_cannot_be_deleted(control) -> None:
    backup = {
        "provider": {
            "kind": "azure_openai",
            "base_url": "https://res.openai.azure.com/openai/v1/",
            "credential": "none",
        },
        "guardrail": "gr-test",
    }
    assert control.put("/v1/gateways/backup", json=backup).status_code == 201
    assert (
        control.put("/v1/gateways/main", json={**GATEWAY, "fallback": "backup"}).status_code == 201
    )
    refused = control.delete("/v1/gateways/backup")
    assert refused.status_code == 409
    assert "main" in refused.json()["detail"]["detail"]
