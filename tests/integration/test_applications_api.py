"""Applications, the AI gateway's providers and defaults through the policy service, keys that
belong to an application (gateway-api 0.10, credentials 0.6, ADR-0039). Synthetic data only."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from umai_decision_engine.applications import AllowList, ApplicationTable
from umai_decision_engine.control_plane.app import create_app
from umai_decision_engine.settings import Settings

APP: dict[str, Any] = {"guardrail": "gr-test", "name": "HR assistant"}
AZURE: dict[str, Any] = {
    "kind": "azure_openai",
    "base_url": "https://contoso-hr.openai.azure.com/openai/v1/",
    "region": "eu",
}


@pytest.fixture
def control(write_bundle, detector, tmp_path):
    write_bundle(bundle_yaml(), active=1)  # owned by the default tenant
    applications_dir = tmp_path / "applications"
    settings = Settings(
        env="development",
        policy_dir=write_bundle.root,
        control_db_path=tmp_path / "control.sqlite",
        applications_dir=applications_dir,
        key_registry_dir=tmp_path / "keys",
    )
    client = TestClient(create_app(settings, detector=detector))
    client.__enter__()
    client.applications_dir = applications_dir  # type: ignore[attr-defined]
    client.settings = settings  # type: ignore[attr-defined]
    yield client
    client.__exit__(None, None, None)


def _table(control) -> ApplicationTable:
    table = ApplicationTable(
        control.applications_dir, AllowList(control.settings.gateway_upstream_allowlist)
    )
    table.reload(force=True)
    return table


def test_applications_round_trip_and_reach_the_engine(control) -> None:
    assert control.get("/v1/applications").json() == {"tenant_id": "default", "applications": []}
    created = control.put("/v1/applications/hr-assistant", json=APP)
    assert created.status_code == 201
    assert created.json()["application"]["application_id"] == "hr-assistant"
    changed = control.put("/v1/applications/hr-assistant", json={**APP, "models": ["gpt-4.1"]})
    assert changed.status_code == 200
    listed = control.get("/v1/applications").json()["applications"]
    assert [a["application_id"] for a in listed] == ["hr-assistant"]
    assert listed[0]["models"] == ["gpt-4.1"]

    path = control.applications_dir / "default.yaml"
    assert (
        yaml.safe_load(path.read_text(encoding="utf-8"))["applications"][0]["guardrail"]
        == "gr-test"
    )
    table = _table(control)
    assert table.application("default", "hr-assistant") is not None and table.errors == []

    assert control.delete("/v1/applications/hr-assistant").json()["deleted"] == "hr-assistant"
    assert _table(control).application("default", "hr-assistant") is None
    assert control.delete("/v1/applications/hr-assistant").status_code == 404


@pytest.mark.parametrize(
    ("body", "error", "fragment"),
    [
        ({**APP, "application_id": "another"}, "invalid_configuration", "path"),
        ({**APP, "providers": ["nowhere"]}, "invalid_configuration", "no provider nowhere"),
        ({**APP, "models": ["bad name"]}, "invalid_configuration", "model name"),
        ({**APP, "fallback": "@nowhere/x"}, "invalid_configuration", "no provider nowhere"),
        ({**APP, "guardrail": "gr-elsewhere"}, "unknown_guardrail", "gr-elsewhere"),
    ],
)
def test_invalid_applications_are_refused_and_nothing_is_written(control, body, error, fragment):
    response = control.put("/v1/applications/hr-assistant", json=body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error"] == error and fragment in detail["detail"]
    assert not (control.applications_dir / "default.yaml").exists()


def test_providers_include_the_builtin_openai_and_one_default(control) -> None:
    listed = control.get("/v1/providers").json()["providers"]
    assert listed == [
        {
            "provider_id": "openai",
            "name": "OpenAI",
            "enabled": True,
            "kind": "openai",
            "base_url": "https://api.openai.com/v1",
            "credential": "pass_through",
            "default": True,
            "builtin": True,
        }
    ]
    assert control.put("/v1/providers/contoso-hr", json=AZURE).status_code == 201
    made_default = control.put("/v1/providers/contoso-hr", json={**AZURE, "default": True})
    assert made_default.status_code == 200 and made_default.json()["provider"]["default"] is True
    by_id = {p["provider_id"]: p for p in control.get("/v1/providers").json()["providers"]}
    assert by_id["openai"]["default"] is False and by_id["contoso-hr"]["builtin"] is False


@pytest.mark.parametrize(
    ("provider_id", "body", "fragment"),
    [
        ("contoso-hr", {**AZURE, "base_url": "https://10.0.0.5/v1"}, "allow-list"),
        ("contoso-hr", {"kind": "azure_openai"}, "base_url"),
        ("contoso-hr", {**AZURE, "credential": "secret"}, "secret"),
        ("openai", {"enabled": False}, "no default provider"),
    ],
)
def test_invalid_providers_are_refused(control, provider_id, body, fragment) -> None:
    response = control.put(f"/v1/providers/{provider_id}", json=body)
    assert response.status_code == 422, response.text
    assert fragment in response.json()["detail"]["detail"]


def test_a_provider_in_use_cannot_be_removed(control) -> None:
    control.put("/v1/providers/contoso-hr", json=AZURE)
    control.put("/v1/applications/hr-assistant", json={**APP, "providers": ["contoso-hr"]})
    refused = control.delete("/v1/providers/contoso-hr")
    assert refused.status_code == 409
    assert "hr-assistant" in refused.json()["detail"]["detail"]
    assert control.delete("/v1/providers/openai").status_code == 404  # built-in: disable it
    control.put("/v1/applications/hr-assistant", json=APP)
    assert control.delete("/v1/providers/contoso-hr").status_code == 200


def test_gateway_settings_are_defaults(control) -> None:
    assert control.get("/v1/gateway/settings").json()["gateway"]["refusal"] == "error"
    saved = control.put(
        "/v1/gateway/settings", json={"refusal": "message", "stream_mode": "buffer"}
    )
    assert saved.status_code == 200
    assert saved.json()["gateway"]["stream_mode"] == "buffer"
    refused = control.put("/v1/gateway/settings", json={"refusal": "shout"})
    assert refused.status_code == 422


def test_keys_belong_to_an_application_that_then_cannot_be_deleted(control) -> None:
    control.put("/v1/applications/hr-assistant", json=APP)
    created = control.post(
        "/v1/tenants/default/keys", json={"name": "hr-bot", "application_id": "hr-assistant"}
    )
    assert created.status_code == 201, created.text
    key = created.json()["key"]
    assert key["application_id"] == "hr-assistant"
    assert sorted(key["scopes"]) == ["evaluate", "gateway"]  # every connection of the application
    listed = control.get("/v1/tenants/default/keys").json()["keys"]
    assert listed[0]["application_id"] == "hr-assistant"
    snapshot = (control.settings.key_registry_dir / "default.json").read_text(encoding="utf-8")
    assert (
        '"application_id": "hr-assistant"' in snapshot and '"contract_version": "0.3"' in snapshot
    )

    refused = control.delete("/v1/applications/hr-assistant")
    assert refused.status_code == 409
    assert refused.json()["detail"]["error"] == "application_in_use"
    control.delete(f"/v1/tenants/default/keys/{key['key_id']}")
    assert control.delete("/v1/applications/hr-assistant").status_code == 200


def test_a_key_needs_an_existing_application(control) -> None:
    response = control.post(
        "/v1/tenants/default/keys", json={"name": "x", "application_id": "nope"}
    )
    assert response.status_code == 422
    assert "unknown_application" in response.text
