"""Gateway routes managed through the policy service (gateway-api 0.7). Synthetic data only."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from umai_decision_engine.control_plane.app import create_app
from umai_decision_engine.gateway.routing import RouteTable
from umai_decision_engine.routefiles import AllowList
from umai_decision_engine.settings import Settings

ROUTE: dict[str, Any] = {
    "route_id": "gpt-eu",
    "upstream": {"kind": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-x"},
    "credential": "pass_through",
    "guardrail": "gr-test",
}


@pytest.fixture
def control(write_bundle, detector, tmp_path):
    write_bundle(bundle_yaml(), active=1)  # owned by the default tenant
    routes_dir = tmp_path / "routes"
    settings = Settings(
        env="development",
        policy_dir=write_bundle.root,
        control_db_path=tmp_path / "control.sqlite",
        gateway_routes_dir=routes_dir,
    )
    client = TestClient(create_app(settings, detector=detector))
    client.__enter__()
    client.routes_dir = routes_dir  # type: ignore[attr-defined]
    client.settings = settings  # type: ignore[attr-defined]
    yield client
    client.__exit__(None, None, None)


def test_routes_round_trip_and_reach_the_gateway(control) -> None:
    assert control.get("/v1/gateway/routes").json() == {"tenant_id": "default", "routes": []}
    response = control.put("/v1/gateway/routes", json={"routes": [ROUTE]})
    assert response.status_code == 200
    assert response.json()["routes"][0]["route_id"] == "gpt-eu"
    assert control.get("/v1/gateway/routes").json()["routes"][0]["upstream"]["model"] == "gpt-x"

    written = yaml.safe_load((control.routes_dir / "default.yaml").read_text(encoding="utf-8"))
    assert written["routes"][0]["guardrail"] == "gr-test"
    table = RouteTable(control.routes_dir, AllowList(control.settings.gateway_upstream_allowlist))
    table.reload(force=True)
    assert table.get("default", "gpt-eu") is not None and table.errors == []

    assert control.put("/v1/gateway/routes", json={"routes": []}).status_code == 200
    table.reload(force=True)
    assert table.get("default", "gpt-eu") is None


@pytest.mark.parametrize(
    ("routes", "error", "fragment"),
    [
        ([{**ROUTE, "credential": "secret"}], "invalid_routes", "secret"),
        (
            [{**ROUTE, "upstream": {**ROUTE["upstream"], "base_url": "https://10.0.0.5/v1"}}],
            "invalid_routes",
            "allow-list",
        ),
        ([ROUTE, ROUTE], "invalid_routes", "duplicate"),
        ([{**ROUTE, "guardrail": "gr-elsewhere"}], "unknown_guardrail", "gr-elsewhere"),
    ],
)
def test_invalid_routes_are_refused_and_nothing_is_written(control, routes, error, fragment):
    response = control.put("/v1/gateway/routes", json={"routes": routes})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == error and fragment in detail["detail"]
    assert not (control.routes_dir / "default.yaml").exists()


def test_body_must_be_json(control) -> None:
    response = control.put("/v1/gateway/routes", content=b"not json")
    assert response.status_code == 422
