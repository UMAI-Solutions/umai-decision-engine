"""Concurrent gateway calls never share placeholders or values (one mask map per call).

Synthetic TCKNs with valid checksums (docs/reference/turkish-identifiers.md algorithm).
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from tests.integration.test_gateway import GUARDRAIL, route_yaml
from umai_decision_engine.gateway.app import create_gateway_app
from umai_decision_engine.settings import Settings


def tckn(first9: str) -> str:
    d = [int(c) for c in first9]
    d10 = (7 * (d[0] + d[2] + d[4] + d[6] + d[8]) - (d[1] + d[3] + d[5] + d[7])) % 10
    d11 = (sum(d) + d10) % 10
    return f"{first9}{d10}{d11}"


SEEN: list[str] = []


def echo(request: httpx.Request) -> httpx.Response:
    """The upstream repeats the (masked) user text, as models often do."""

    body = json.loads(request.content)
    text = body["messages"][-1]["content"]
    SEEN.append(text)
    message = {"role": "assistant", "content": f"Aldım: {text}"}
    return httpx.Response(
        200,
        json={
            "id": "c",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-x",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        },
    )


@pytest.mark.asyncio
async def test_concurrent_calls_keep_their_own_values(write_bundle, detector, tmp_path) -> None:
    SEEN.clear()
    write_bundle(GUARDRAIL.format(mode="ENFORCE"), bundle_id="gr-gw", active=1)
    routes = tmp_path / "routes"
    routes.mkdir()
    (routes / "tenant-a.yaml").write_text(route_yaml(), encoding="utf-8")
    settings = Settings(
        env="development",
        policy_dir=write_bundle.root,
        bundle_poll_interval_s=0,
        l0_threads=2,
        default_tenant_id="tenant-a",
        gateway_routes_dir=routes,
        gateway_routes_poll_interval_s=0,
        rate_limit_rps=None,
    )
    app = create_gateway_app(
        settings, detector=detector, upstream_transport=httpx.MockTransport(echo)
    )
    values = [tckn(f"10{n:07d}") for n in range(100, 120)]

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:

            async def call(value: str) -> str:
                response = await client.post(
                    "/v1/chat/completions",
                    headers={"X-Umai-Provider-Key": "sk-test"},
                    json={
                        "model": "gpt-eu",
                        "messages": [{"role": "user", "content": f"TC kimlik {value} kayıt"}],
                    },
                )
                assert response.status_code == 200
                return response.json()["choices"][0]["message"]["content"]

            answers = await asyncio.gather(*(call(v) for v in values))

    assert len(SEEN) == len(values)
    assert all(text == "TC kimlik <TR_TCKN_1> kayıt" for text in SEEN)  # masked upstream
    for value, answer in zip(values, answers, strict=True):
        assert answer == f"Aldım: TC kimlik {value} kayıt"
        assert not [other for other in values if other != value and other in answer]
