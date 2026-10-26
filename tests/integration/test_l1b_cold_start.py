"""L1b cold start (ADR-0040 amendment 2026-10-26): the process opens its provider connection at
startup, and the first decisions reuse it. A local HTTP server stands in for the provider and
counts TCP connections; no network. Synthetic data."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from tests.conftest import bundle_yaml
from umai_decision_engine.contracts.questions import (
    DecisionQuestion,
    DecisionRequest,
    ExcerptMessage,
)
from umai_decision_engine.l1.decision_models.fake import FakeDecisionModel
from umai_decision_engine.l1.decision_models.registry import DecisionModels
from umai_decision_engine.runtime import start_runtime
from umai_decision_engine.settings import Settings

REQUEST = DecisionRequest(
    request_id="req-cold",
    policies=[{"id": "pol", "version": 1, "policy_text": "Synthetic."}],
    facts={"phase": "PRE_LLM"},
    excerpt=[ExcerptMessage(role="user", content="Merhaba")],
    questions=[DecisionQuestion(qid="pol.q", type="condition", instructions="Is it a greeting?")],
)
ANSWER = {
    "model": "gpt-6-luna",
    "answers": [{"type": "predicate", "name": "pol.q", "probability": 0.9}],
}


class Provider:
    """Minimal HTTP/1.1 keep-alive server: one answer to every request."""

    def __init__(self) -> None:
        self.connections = 0
        self.requests: list[str] = []

    async def __aenter__(self) -> Provider:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.base_url = f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}/v1"
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._server.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            while head := await reader.readuntil(b"\r\n\r\n"):
                lines = head.decode("latin-1").split("\r\n")
                fields = (line.partition(":") for line in lines[1:])
                headers = {name.lower(): value.strip() for name, _, value in fields}
                await reader.readexactly(int(headers.get("content-length") or 0))
                method, path, _ = lines[0].split(" ", 2)
                self.requests.append(f"{method} {path}")
                body = json.dumps(ANSWER).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode()
                    + body
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


def settings_for(provider: Provider, **overrides: object) -> Settings:
    return Settings(
        decision_providers={
            "openai": {"adapter": "openai_decisions", "base_url": provider.base_url}
        },
        decision_provider_keys={"openai": "sk-test-not-real"},
        **overrides,
    )


async def test_the_first_decisions_reuse_the_connection_opened_at_warm_up() -> None:
    async with Provider() as provider:
        models = DecisionModels.from_settings(settings_for(provider))
        assert await models.warm_up(1.0) == {"openai": "ok"}
        assert (provider.connections, provider.requests) == (1, ["POST /v1/decisions"])
        model = models.get("openai")
        for _ in range(3):
            result = await model.decide(REQUEST, timeout_s=1.0)
            assert result.answers["pol.q"].probability == pytest.approx(0.9)
        assert provider.connections == 1
        await models.aclose()


async def test_idle_connections_are_kept_for_the_configured_time() -> None:
    async with Provider() as provider:
        models = DecisionModels.from_settings(settings_for(provider, decision_keepalive_s=1))
        await models.warm_up(1.0)
        await asyncio.sleep(1.2)
        await models.get("openai").decide(REQUEST, timeout_s=1.0)
        assert provider.connections == 2  # expired after 1 s; httpx's own default is 5 s
        await models.aclose()


async def test_a_failed_warm_up_is_logged_and_startup_goes_on(caplog) -> None:
    hang_up = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = hang_up.sockets[0].getsockname()[1]
    models = DecisionModels.from_settings(
        Settings(
            decision_providers={
                "openai": {"adapter": "openai_decisions", "base_url": f"http://127.0.0.1:{port}/v1"}
            },
            decision_provider_keys={"openai": "sk-test-not-real"},
        )
    )
    with caplog.at_level(logging.INFO, logger="umai.de.l1b"):
        assert await models.warm_up(1.0) == {"openai": "unavailable"}
    hang_up.close()
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert "l1b.warm_up provider=openai adapter=openai_decisions outcome=unavailable" in (
        record.getMessage()
    )
    assert "sk-test" not in caplog.text
    await models.aclose()


@pytest.mark.parametrize(("timeout_ms", "warmed"), [(2000, 1), (0, 0)])
async def test_the_runtime_warms_providers_before_taking_traffic(
    write_bundle, detector, timeout_ms, warmed
) -> None:
    write_bundle(bundle_yaml(), active=1)
    fake = FakeDecisionModel("openai")
    runtime = await start_runtime(
        Settings(
            policy_dir=write_bundle.root,
            bundle_poll_interval_s=0,
            l0_threads=1,
            decision_warm_up_timeout_ms=timeout_ms,
        ),
        detector=detector,
        decision_models=DecisionModels({"openai": fake}),
    )
    assert fake.warm_ups == warmed
    await runtime.stop()
