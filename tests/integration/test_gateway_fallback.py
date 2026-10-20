"""Fallback (gateway-api 0.6, 0.10): one retry on another provider, same masked body and guardrail.
Synthetic content only.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.integration.test_gateway import GUARDRAIL, TCKN, completion, text_stream
from umai_decision_engine.gateway.app import create_gateway_app
from umai_decision_engine.settings import Settings

BACKUP = {
    "provider_id": "azure-backup",
    "kind": "azure_openai",
    "base_url": "https://res.openai.azure.com/openai/v1/",
    "credential": "secret",
    "secret": "azure",
}
APP = {
    "application_id": "app-eu",
    "guardrail": "gr-gw",
    "aliases": [{"name": "gpt-eu", "model": "gpt-x"}],
    "fallback": "@azure-backup/gpt-x-deployment",
}


class Upstreams:
    def __init__(self) -> None:
        self.primary: Any = 503  # status code, or an exception to raise
        self.seen: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.seen.append((request.url.host, body))
        if request.url.host == "api.openai.com":
            if isinstance(self.primary, Exception):
                raise self.primary
            if self.primary != 200:
                return httpx.Response(self.primary, json={"error": {"message": "x", "code": "x"}})
        if body.get("stream"):
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=text_stream("Yedek.")
            )
        return httpx.Response(200, json=completion(f"{request.url.host}: <TR_TCKN_1>"))


@pytest.fixture
def gateway(write_bundle, detector, tmp_path):
    clients: list[TestClient] = []
    upstreams = Upstreams()

    def _make(content: dict[str, Any]) -> tuple[TestClient, Upstreams]:
        write_bundle(GUARDRAIL.format(mode="ENFORCE"), bundle_id="gr-gw", active=1)
        directory = tmp_path / "applications"
        directory.mkdir(exist_ok=True)
        (directory / "tenant-a.yaml").write_text(json.dumps(content), encoding="utf-8")
        settings = Settings(
            env="development",
            policy_dir=write_bundle.root,
            bundle_poll_interval_s=0,
            l0_threads=1,
            default_tenant_id="tenant-a",
            applications_dir=directory,
            applications_poll_interval_s=0,
            gateway_provider_keys={"azure": "az-secret"},
        )
        app = create_gateway_app(
            settings, detector=detector, upstream_transport=httpx.MockTransport(upstreams.handler)
        )
        client = TestClient(app, headers={"X-Umai-Provider-Key": "sk-test"})
        client.__enter__()
        clients.append(client)
        return client, upstreams

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


def ask(client: TestClient, **extra: Any) -> httpx.Response:
    return client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-eu",
            "messages": [{"role": "user", "content": f"TC kimlik {TCKN}"}],
            **extra,
        },
    )


@pytest.mark.parametrize(
    "failure", [503, 429, httpx.ConnectError("down"), httpx.ReadTimeout("slow")]
)
def test_falls_back_once_with_the_same_masked_body(gateway, failure) -> None:
    client, upstreams = gateway({"providers": [BACKUP], "applications": [APP]})
    upstreams.primary = failure
    response = ask(client)
    assert response.status_code == 200
    assert response.headers["X-Umai-Fallback"] == "app-eu"
    assert response.json()["choices"][0]["message"]["content"] == f"res.openai.azure.com: {TCKN}"
    hosts = [host for host, _ in upstreams.seen]
    assert hosts == ["api.openai.com", "res.openai.azure.com"]
    first, second = (body for _, body in upstreams.seen)
    assert first["messages"] == second["messages"]  # masked once, sent twice
    assert TCKN not in json.dumps(second)
    assert second["model"] == "gpt-x-deployment"


def test_client_errors_do_not_fall_back(gateway) -> None:
    client, upstreams = gateway({"providers": [BACKUP], "applications": [APP]})
    upstreams.primary = 400
    response = ask(client)
    assert response.status_code == 400
    assert "X-Umai-Fallback" not in response.headers
    assert [host for host, _ in upstreams.seen] == ["api.openai.com"]


def test_streams_fall_back_before_the_first_byte(gateway) -> None:
    client, _ = gateway({"providers": [BACKUP], "applications": [APP]})
    response = ask(client, stream=True)
    assert response.status_code == 200
    assert response.headers["X-Umai-Fallback"] == "app-eu"
    assert "Yedek." in response.text


PASS_THROUGH_BACKUP = {k: v for k, v in BACKUP.items() if k not in ("credential", "secret")}


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        ({"applications": [{**APP, "fallback": "@missing/x"}]}, "no provider missing"),
        (
            {"providers": [PASS_THROUGH_BACKUP], "applications": [APP]},
            "cannot pass the caller's provider key",
        ),
        (
            {"providers": [BACKUP], "applications": [{**APP, "providers": ["openai"]}]},
            "may not use that provider",
        ),
    ],
)
def test_invalid_fallbacks_do_not_load(gateway, content, problem) -> None:
    client, _ = gateway(content)
    assert client.get("/v1/models").json()["error"]["code"] == "unknown_application"
    assert problem in client.app.state.applications.errors[0][1]
