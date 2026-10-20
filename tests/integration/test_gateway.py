"""UMAI gateway end to end against a fake upstream (contract gateway-api; no network).

All content is synthetic (AGENTS rule 2): the TCKN and card numbers are test vectors.
"""

from __future__ import annotations

import json
import logging
import textwrap
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from umai_decision_engine.contracts.credentials import (
    KeyRegistryEntry,
    KeyRegistrySnapshot,
    Scope,
)
from umai_decision_engine.contracts.gateway import GatewayErrorBody
from umai_decision_engine.gateway.app import create_gateway_app
from umai_decision_engine.settings import Settings
from umai_decision_engine.tenancy.keys import generate_key

TCKN = "10000000146"
CARD = "4242 4242 4242 4242"
EMAIL = "ayse.demir@example.com"

GUARDRAIL = """\
contract_version: "0.6"
id: gr-gw
version: 1
mode: {mode}
phases: [PRE_LLM, POST_LLM, TOOL_INPUT, TOOL_OUTPUT]
on_evaluation_failure: fail_closed
l0:
  policies:
    - id: identity
      entities: [TR_TCKN]
      action: ALLOW_WITH_MODIFICATIONS
      severity: HIGH
    - id: contact
      entities: [EMAIL_ADDRESS]
      action: ALLOW_WITH_MODIFICATIONS
      severity: MEDIUM
    - id: payment
      entities: [CREDIT_CARD]
      action: BLOCK
      severity: HIGH
"""


# Agents may send national ids to their own tools (no tool_call role on identity); e-mail
# addresses in results of the CRM tool are trusted (an exception on the tool's name).
AGENT_GUARDRAIL = """\
contract_version: "0.6"
id: gr-gw
version: 1
mode: ENFORCE
phases: [PRE_LLM, POST_LLM, TOOL_INPUT, TOOL_OUTPUT]
on_evaluation_failure: fail_closed
l0:
  policies:
    - id: identity
      entities: [TR_TCKN]
      action: ALLOW_WITH_MODIFICATIONS
      roles: [user, assistant, tool]
    - id: contact
      entities: [EMAIL_ADDRESS]
      action: ALLOW_WITH_MODIFICATIONS
    - id: payment
      entities: [CREDIT_CARD]
      action: BLOCK
  exceptions:
    - id: ex-crm-tool
      when: { all: [ { field: action.tool_name, operator: EQUALS, value: crm_lookup } ] }
      entities: [EMAIL_ADDRESS]
      action: ALLOW
"""

# No tool phases: tool results fall back to the input check.
CHAT_ONLY_GUARDRAIL = """\
contract_version: "0.6"
id: gr-gw
version: 1
mode: ENFORCE
phases: [PRE_LLM, POST_LLM]
on_evaluation_failure: fail_closed
l0:
  policies:
    - id: contact
      entities: [EMAIL_ADDRESS]
      action: ALLOW_WITH_MODIFICATIONS
"""


AZURE = {
    "provider_id": "azure-eu",
    "kind": "azure_openai",
    "base_url": "https://res.openai.azure.com/openai/v1/",
}


def application(**overrides: Any) -> dict[str, Any]:
    """One application, ``app-eu``, guardrail ``gr-gw``; its calls go to the built-in OpenAI
    provider. The alias ``gpt-eu`` → ``gpt-x`` keeps the provider model visible in assertions."""

    return {
        "application_id": "app-eu",
        "guardrail": "gr-gw",
        "aliases": [{"name": "gpt-eu", "model": "gpt-x"}],
        **overrides,
    }


def app_yaml(
    *apps: dict[str, Any],
    providers: list[dict[str, Any]] | None = None,
    gateway: dict[str, Any] | None = None,
    **overrides: Any,
) -> str:
    data: dict[str, Any] = {"applications": list(apps) or [application(**overrides)]}
    if providers:
        data["providers"] = providers
    if gateway:
        data["gateway"] = gateway
    return json.dumps(data)


def completion(content: str | None, *, tool_calls: list | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content, "refusal": None}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-upstream",
        "object": "chat.completion",
        "created": 1760870000,
        "model": "gpt-x",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def sse(*chunks: dict[str, Any] | str) -> bytes:
    out = []
    for chunk in chunks:
        payload = chunk if isinstance(chunk, str) else json.dumps(chunk)
        out.append(f"data: {payload}\n\n")
    return "".join(out).encode()


def chunk(delta: dict[str, Any], finish: str | None = None, index: int = 0) -> dict[str, Any]:
    return {
        "id": "chatcmpl-upstream",
        "object": "chat.completion.chunk",
        "created": 1760870000,
        "model": "gpt-x",
        "choices": [{"index": index, "delta": delta, "finish_reason": finish}],
    }


def text_stream(*pieces: str, usage: bool = True) -> bytes:
    chunks: list[dict[str, Any] | str] = [chunk({"role": "assistant", "content": ""})]
    chunks += [chunk({"content": p}) for p in pieces]
    chunks.append(chunk({}, "stop"))
    if usage:
        chunks.append(
            {
                "id": "chatcmpl-upstream",
                "object": "chat.completion.chunk",
                "created": 1760870000,
                "model": "gpt-x",
                "choices": [],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }
        )
    chunks.append("[DONE]")
    return sse(*chunks)


class FakeUpstream:
    """Records what the gateway sends; answers with ``reply`` (JSON) or ``stream`` (SSE)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.reply: Callable[[dict], dict] = lambda body: completion("Tamam.")
        self.stream: bytes = text_stream("Tamam.")
        self.status = 200
        self.error: Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        body = json.loads(request.content) if request.content else {}
        if body.get("stream"):
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=self.stream
            )
        return httpx.Response(self.status, json=self.reply(body), headers={"x-request-id": "up-1"})

    @property
    def last_body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()


@pytest.fixture
def make_gateway(write_bundle, detector, tmp_path, upstream):
    clients: list[TestClient] = []

    def _make(
        *,
        mode: str = "ENFORCE",
        applications: str | None = None,
        guardrail: str | None = None,
        decision_models: Any = None,
        **settings: Any,
    ) -> TestClient:
        write_bundle(guardrail or GUARDRAIL.format(mode=mode), bundle_id="gr-gw", active=1)
        applications_dir = tmp_path / "applications"
        applications_dir.mkdir(exist_ok=True)
        (applications_dir / "tenant-a.yaml").write_text(
            applications or app_yaml(), encoding="utf-8"
        )
        settings.setdefault("env", "development")
        cfg = Settings(
            policy_dir=write_bundle.root,
            bundle_poll_interval_s=0,
            l0_threads=1,
            default_tenant_id="tenant-a",
            applications_dir=applications_dir,
            applications_poll_interval_s=0,
            **settings,
        )
        app = create_gateway_app(
            cfg,
            detector=detector,
            upstream_transport=httpx.MockTransport(upstream.handler),
            decision_models=decision_models,
        )
        client = TestClient(app, headers={"X-Umai-Provider-Key": "sk-test"})
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


def ask(client: TestClient, text: str, **extra: Any) -> httpx.Response:
    body = {"model": "gpt-eu", "messages": [{"role": "user", "content": text}], **extra}
    return client.post("/v1/chat/completions", json=body)


def stream_events(response: httpx.Response) -> list[Any]:
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            payload = line[len("data: ") :]
            events.append(payload if payload == "[DONE]" else json.loads(payload))
    return events


def streamed_text(events: list[Any], index: int = 0) -> str:
    return "".join(
        c["choices"][0]["delta"].get("content") or ""
        for c in events
        if isinstance(c, dict) and c.get("choices") and c["choices"][0]["index"] == index
    )


def finish_reasons(events: list[Any]) -> list[str]:
    return [
        c["choices"][0]["finish_reason"]
        for c in events
        if isinstance(c, dict) and c.get("choices") and c["choices"][0]["finish_reason"]
    ]


# ------------------------------------------------------------------ applications and admission


def test_models_lists_the_applications_names(make_gateway) -> None:
    client = make_gateway()
    body = client.get("/v1/models").json()
    assert body == {
        "object": "list",
        "data": [{"id": "gpt-eu", "object": "model", "created": 0, "owned_by": "umai"}],
    }


def test_a_model_outside_the_list_is_404_in_openai_shape(make_gateway, upstream) -> None:
    client = make_gateway(applications=app_yaml(models=["gpt-4.1-mini"]))
    response = client.post(
        "/v1/chat/completions",
        json={"model": "nope", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 404
    body = GatewayErrorBody.model_validate(response.json())
    assert body.error.code == "model_not_allowed"
    assert upstream.requests == []
    for allowed in ("gpt-4.1-mini", "gpt-eu"):  # listed, or an alias
        ok = client.post(
            "/v1/chat/completions",
            json={"model": allowed, "messages": [{"role": "user", "content": "x"}]},
        )
        assert ok.status_code == 200


def test_any_model_passes_through_to_the_default_provider(make_gateway, upstream) -> None:
    client = make_gateway()
    response = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 200
    assert response.headers["X-Umai-Application"] == "app-eu"
    assert upstream.last_body["model"] == "gpt-4.1-mini"
    assert str(upstream.requests[-1].url) == "https://api.openai.com/v1/chat/completions"
    assert ask(client, "x").status_code == 200
    assert upstream.last_body["model"] == "gpt-x"  # the alias


def test_the_provider_prefix_picks_another_provider(make_gateway, upstream) -> None:
    client = make_gateway(applications=app_yaml(providers=[AZURE]))
    body = {"model": "@azure-eu/hr-deploy", "messages": [{"role": "user", "content": "x"}]}
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200
    request = upstream.requests[-1]
    assert str(request.url) == "https://res.openai.azure.com/openai/v1/chat/completions"
    assert upstream.last_body["model"] == "hr-deploy"
    assert request.headers["api-key"] == "sk-test"  # pass-through, Azure's header
    unknown = client.post("/v1/chat/completions", json={**body, "model": "@nowhere/x"})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "unknown_provider"


def test_an_application_limited_to_providers(make_gateway, upstream) -> None:
    client = make_gateway(
        applications=app_yaml(application(providers=["azure-eu"]), providers=[AZURE])
    )
    refused = ask(client, "x")  # alias gpt-eu → gpt-x on the default provider, OpenAI
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "provider_not_allowed"
    body = {"model": "@azure-eu/hr-deploy", "messages": [{"role": "user", "content": "x"}]}
    assert client.post("/v1/chat/completions", json=body).status_code == 200


def test_a_model_name_with_free_text_is_refused_before_any_log(make_gateway, upstream) -> None:
    client = make_gateway()
    response = client.post(
        "/v1/chat/completions",
        json={"model": f"TC {TCKN}", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["param"] == "model"
    assert upstream.requests == []


def test_models_without_a_list_are_the_providers(make_gateway, upstream) -> None:
    client = make_gateway(applications=app_yaml(aliases=[]))
    upstream.reply = lambda body: {"object": "list", "data": [{"id": "gpt-4.1-mini"}]}
    response = client.get("/v1/models")
    assert response.status_code == 200
    assert response.json()["data"] == [{"id": "gpt-4.1-mini"}]
    request = upstream.requests[-1]
    assert request.method == "GET" and str(request.url) == "https://api.openai.com/v1/models"
    assert request.headers["authorization"] == "Bearer sk-test"


TWO_APPLICATIONS = (
    application(),
    application(application_id="app-hr", providers=["azure-eu"]),
)


def test_several_applications_need_a_name(make_gateway, upstream, caplog) -> None:
    client = make_gateway(applications=app_yaml(*TWO_APPLICATIONS, providers=[AZURE]))
    with caplog.at_level(logging.INFO, logger="umai.gateway"):
        response = ask(client, "selam")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "application_required"
    assert "gateway.refused" in caplog.text and "code=application_required" in caplog.text
    assert "selam" not in caplog.text
    named = client.post(
        "/v1/chat/completions",
        json={"model": "@azure-eu/hr", "messages": [{"role": "user", "content": "selam"}]},
        headers={"X-Umai-Application": "app-hr"},
    )
    assert named.status_code == 200
    assert named.headers["X-Umai-Application"] == "app-hr"
    assert upstream.requests[-1].url.host == "res.openai.azure.com"
    unknown = client.get("/v1/models", headers={"X-Umai-Application": "app-gone"})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "unknown_application"


def _registry(tmp_path, *keys: tuple[Any, str | None]) -> Any:
    directory = tmp_path / "keys"
    directory.mkdir(exist_ok=True)
    snapshot = KeyRegistrySnapshot(
        tenant_id="tenant-a",
        generated_at=datetime.now(UTC),
        keys=[
            KeyRegistryEntry(
                key_id=k.key_id,
                secret_sha256=k.secret_sha256,
                env="live",
                region="sh",
                scopes=[Scope.GATEWAY, Scope.EVALUATE],
                application_id=application_id,
                status="active",
            )
            for k, application_id in keys
        ],
    )
    (directory / "tenant-a.json").write_text(snapshot.model_dump_json(), encoding="utf-8")
    return directory


def test_a_key_uses_its_application_and_no_other(make_gateway, upstream, tmp_path) -> None:
    bound, orphan = generate_key("live", "sh"), generate_key("live", "sh")
    client = make_gateway(
        applications=app_yaml(*TWO_APPLICATIONS, providers=[AZURE]),
        env="test",
        key_registry_dir=_registry(tmp_path, (bound, "app-hr"), (orphan, "app-gone")),
        require_signed_bundles=False,
    )
    body = {"model": "@azure-eu/hr", "messages": [{"role": "user", "content": "selam"}]}
    as_bound = {"Authorization": f"Bearer {bound.presentation}"}
    response = client.post("/v1/chat/completions", json=body, headers=as_bound)
    assert response.status_code == 200
    assert upstream.requests[-1].url.host == "res.openai.azure.com"
    other = client.post(
        "/v1/chat/completions", json=body, headers={**as_bound, "X-Umai-Application": "app-eu"}
    )
    assert other.status_code == 403
    assert other.json()["error"]["code"] == "application_mismatch"
    gone = client.post(
        "/v1/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {orphan.presentation}"},
    )
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "unknown_application"


def test_a_provider_outside_the_allow_list_is_not_loaded(make_gateway) -> None:
    evil = {"provider_id": "evil", "kind": "openai", "base_url": "https://evil.example/v1"}
    client = make_gateway(applications=app_yaml(providers=[evil]))
    response = client.get("/v1/models")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_application"
    errors = client.app.state.applications.errors
    assert errors and "allow-list" in errors[0][1]


def test_key_needs_the_gateway_scope(make_gateway, tmp_path) -> None:
    allowed, plain = generate_key("live", "sh"), generate_key("live", "sh")
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
                scopes=scopes,
                status="active",
            )
            for k, scopes in ((allowed, [Scope.GATEWAY]), (plain, [Scope.EVALUATE]))
        ],
    )
    (keys / "tenant-a.json").write_text(snapshot.model_dump_json(), encoding="utf-8")
    client = make_gateway(env="test", key_registry_dir=keys, require_signed_bundles=False)

    def _call(key: str | None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return client.get("/v1/models", headers=headers)

    assert _call(None).status_code == 401
    assert _call(None).json()["error"]["code"] == "invalid_credential"
    assert _call(plain.presentation).status_code == 403
    assert _call(plain.presentation).json()["error"]["code"] == "insufficient_scope"
    assert _call(allowed.presentation).status_code == 200


# ------------------------------------------------------------------ input checks


def test_clean_prompt_is_forwarded_unchanged(make_gateway, upstream) -> None:
    client = make_gateway()
    response = ask(client, "Toplantı notlarını özetler misin?", temperature=0.2)
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Tamam."
    sent = upstream.last_body
    assert sent["model"] == "gpt-x"  # route id → upstream model
    assert sent["temperature"] == 0.2  # unknown fields pass through
    assert sent["messages"][0]["content"] == "Toplantı notlarını özetler misin?"
    assert response.headers["X-Umai-Input-Decision"] == "ALLOW"
    assert response.headers["X-Umai-Event-Id"].startswith("ta-")
    assert response.headers["x-request-id"] == "up-1"  # provider headers pass on
    request = upstream.requests[-1]
    assert request.headers["authorization"] == "Bearer sk-test"
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"


def test_identifier_is_masked_upstream_and_restored_for_the_caller(make_gateway, upstream) -> None:
    client = make_gateway()
    upstream.reply = lambda body: completion("Kimlik numarası <TR_TCKN_1> kayıtlı.")
    response = ask(client, f"TC kimlik {TCKN} olan müşteriyi kontrol et")
    assert response.status_code == 200
    sent = upstream.last_body["messages"][0]["content"]
    assert TCKN not in sent and "<TR_TCKN_1>" in sent
    assert response.json()["choices"][0]["message"]["content"] == f"Kimlik numarası {TCKN} kayıtlı."
    assert response.headers["X-Umai-Input-Decision"] == "ALLOW_WITH_MODIFICATIONS"


def test_same_value_same_placeholder_across_messages(make_gateway, upstream) -> None:
    client = make_gateway()
    body = {
        "model": "gpt-eu",
        "messages": [
            {"role": "system", "content": f"Müşteri: {EMAIL}"},
            {"role": "user", "content": [{"type": "text", "text": f"{EMAIL} adresine yaz"}]},
        ],
    }
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    messages = upstream.last_body["messages"]
    assert messages[0]["content"] == "Müşteri: <EMAIL_ADDRESS_1>"
    assert messages[1]["content"][0]["text"] == "<EMAIL_ADDRESS_1> adresine yaz"


def test_blocked_input_never_reaches_the_upstream(make_gateway, upstream) -> None:
    client = make_gateway()
    response = ask(client, f"Kart numaram {CARD}, ödemeyi yap")
    assert response.status_code == 400
    body = GatewayErrorBody.model_validate(response.json())
    assert body.error.code == "umai_blocked" and body.error.type == "umai_guardrail"
    assert body.error.umai is not None and body.error.umai.entities == ["CREDIT_CARD"]
    assert body.error.umai.matched == ["payment"]
    assert CARD.replace(" ", "") not in response.text and CARD not in response.text
    assert upstream.requests == []
    assert response.headers["X-Umai-Input-Decision"] == "BLOCK"


def test_refusal_message_style_answers_200(make_gateway, upstream) -> None:
    client = make_gateway(applications=app_yaml(refusal="message", refusal_text="Engellendi."))
    response = ask(client, f"Kart {CARD}")
    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["message"]["content"] == "Engellendi."
    assert choice["finish_reason"] == "content_filter"
    assert upstream.requests == []


def test_monitor_mode_forwards_unchanged(make_gateway, upstream) -> None:
    client = make_gateway(mode="MONITOR")
    response = ask(client, f"Kart {CARD} ve TC {TCKN}")
    assert response.status_code == 200
    assert CARD in upstream.last_body["messages"][0]["content"]
    assert response.headers["X-Umai-Input-Decision"] == "ALLOW"


def test_unscannable_parts_follow_the_route(make_gateway, upstream) -> None:
    image = {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}
    body = {
        "model": "gpt-eu",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Bu ne?"}, image]}],
    }
    client = make_gateway()
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 400 and response.json()["error"]["code"] == "unsupported_content"

    client = make_gateway(applications=app_yaml(unscanned_parts="pass_unscanned"))
    response = client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200
    assert response.headers["X-Umai-Unscanned-Parts"] == "1"
    assert upstream.last_body["messages"][0]["content"][1] == image


def test_history_tool_call_arguments_are_masked(make_gateway, upstream) -> None:
    client = make_gateway()
    arguments = json.dumps({"tckn": TCKN})
    body = {
        "model": "gpt-eu",
        "messages": [
            {"role": "user", "content": "Müşteriyi bul"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "find_customer", "arguments": arguments},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": f"Bulundu: TC kimlik {TCKN}"},
        ],
    }
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    sent = upstream.last_body["messages"]
    assert json.loads(sent[1]["tool_calls"][0]["function"]["arguments"]) == {"tckn": "<TR_TCKN_1>"}
    assert sent[2]["content"] == "Bulundu: TC kimlik <TR_TCKN_1>"


def test_a_custom_tool_call_in_the_history_is_masked_again(make_gateway, upstream) -> None:
    # Answers get the caller's values back in custom tool calls; when the agent resends that
    # message, the value must not reach the provider raw (review of PR #12, ADR-0005).
    client = make_gateway(guardrail=AGENT_GUARDRAIL)
    body = {
        "model": "gpt-eu",
        "messages": [
            {"role": "user", "content": "Müşteriyi bul"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "custom",
                        "custom": {"name": "lookup", "input": f"kimlik {TCKN} ve {EMAIL}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "Bulundu."},
        ],
    }
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    sent = upstream.last_body["messages"][1]["tool_calls"][0]["custom"]["input"]
    assert EMAIL not in sent and TCKN not in sent and "<EMAIL_ADDRESS_1>" in sent


def test_size_and_shape_limits(make_gateway) -> None:
    client = make_gateway(gateway_max_input_chars=1_000)
    assert ask(client, "x" * 1_001).status_code == 413
    response = client.post("/v1/chat/completions", content=b"{not json")
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_request"
    response = client.post("/v1/chat/completions", json={"model": "gpt-eu", "messages": []})
    assert response.status_code == 400


def test_a_long_conversation_has_the_gateway_limit(make_gateway, upstream) -> None:
    """The engine API's input limit is not the gateway's: a conversation grows with every
    turn and is resent whole (gateway-api 0.8)."""

    client = make_gateway(max_input_chars=1_000, gateway_max_input_chars=5_000)
    turns = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "haftalık rapor özeti " * 20}
        for i in range(9)
    ]
    turns[-1]["content"] += " TC kimlik 10000000146"
    response = client.post("/v1/chat/completions", json={"model": "gpt-eu", "messages": turns})
    assert response.status_code == 200
    assert upstream.last_body["messages"][-1]["content"].endswith("TC kimlik <TR_TCKN_1>")


# ------------------------------------------------------------------ credentials and upstream


def test_pass_through_needs_the_provider_key(make_gateway, upstream) -> None:
    client = make_gateway()
    body = {"model": "gpt-eu", "messages": [{"role": "user", "content": "selam"}]}
    response = client.post("/v1/chat/completions", json=body, headers={"X-Umai-Provider-Key": ""})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "provider_key_required"


def test_azure_and_secret_credentials(make_gateway, upstream) -> None:
    azure = {**AZURE, "credential": "secret", "secret": "azure-eu", "default": True}
    client = make_gateway(
        applications=app_yaml(providers=[azure]), gateway_provider_keys={"azure-eu": "az-secret"}
    )
    assert ask(client, "selam").status_code == 200
    request = upstream.requests[-1]
    assert request.headers["api-key"] == "az-secret"
    assert "authorization" not in request.headers
    assert str(request.url) == "https://res.openai.azure.com/openai/v1/chat/completions"


def test_missing_deployment_secret_is_a_clear_error(make_gateway) -> None:
    openai = {"provider_id": "openai", "credential": "secret", "secret": "absent"}
    client = make_gateway(applications=app_yaml(providers=[openai]))
    response = ask(client, "selam")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "provider_misconfigured"


def test_upstream_errors_pass_through(make_gateway, upstream) -> None:
    client = make_gateway()
    upstream.status = 429
    upstream.reply = lambda body: {"error": {"message": "slow down", "code": "rate_limit"}}
    response = ask(client, "selam")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limit"
    assert response.headers["X-Umai-Upstream-Error"] == "true"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (httpx.ConnectError("refused"), 502, "upstream_unreachable"),
        (httpx.ReadTimeout("slow"), 504, "upstream_timeout"),
    ],
)
def test_upstream_failures_are_gateway_errors(make_gateway, upstream, error, status, code) -> None:
    client = make_gateway()
    upstream.error = error
    response = ask(client, "selam")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


# ------------------------------------------------------------------ answer checks


def test_model_generated_identifier_stays_masked(make_gateway, upstream) -> None:
    client = make_gateway()
    upstream.reply = lambda body: completion(f"İletişim: {EMAIL}")
    response = ask(client, "Destek ekibine nasıl ulaşırım?")
    content = response.json()["choices"][0]["message"]["content"]
    assert content == "İletişim: <EMAIL_ADDRESS_1>"
    assert response.headers["X-Umai-Output-Decision"] == "ALLOW_WITH_MODIFICATIONS"


def test_blocked_answer_is_refused(make_gateway, upstream) -> None:
    client = make_gateway()
    upstream.reply = lambda body: completion(f"Kart: {CARD}")
    response = ask(client, "Bir test kartı yaz")
    assert response.status_code == 400
    body = GatewayErrorBody.model_validate(response.json())
    assert body.error.umai is not None and body.error.umai.phase == "POST_LLM"
    assert CARD not in response.text

    client = make_gateway(applications=app_yaml(refusal="message"))
    response = ask(client, "Bir test kartı yaz")
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "content_filter" and CARD not in response.text


def test_tool_call_arguments_get_the_callers_value_back(make_gateway, upstream) -> None:
    client = make_gateway(guardrail=AGENT_GUARDRAIL)
    tool_calls = [
        {
            "id": "call_9",
            "type": "function",
            "function": {"name": "find_customer", "arguments": '{"tckn": "<TR_TCKN_1>"}'},
        }
    ]
    upstream.reply = lambda body: completion(None, tool_calls=tool_calls)
    response = ask(client, f"TC kimlik {TCKN} olan müşteriyi bul")
    arguments = response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"tckn": TCKN}


def test_restoration_can_be_switched_off(make_gateway, upstream) -> None:
    client = make_gateway(applications=app_yaml(restore_placeholders=False))
    upstream.reply = lambda body: completion("<TR_TCKN_1> kayıtlı.")
    response = ask(client, f"TC kimlik {TCKN}")
    assert response.json()["choices"][0]["message"]["content"] == "<TR_TCKN_1> kayıtlı."


def test_logs_carry_no_content(make_gateway, upstream, caplog) -> None:
    client = make_gateway()
    upstream.reply = lambda body: completion(f"<TR_TCKN_1> ve {EMAIL}")
    with caplog.at_level(logging.DEBUG):
        ask(client, f"TC kimlik {TCKN}, e-posta {EMAIL}")
        ask(client, f"Kart {CARD}")
    text = caplog.text
    assert "gateway.call" in text
    for secret in (TCKN, EMAIL, CARD, CARD.replace(" ", ""), "sk-test"):
        assert secret not in text


# ------------------------------------------------------------------ streaming


@pytest.mark.parametrize("mode", ["window", "buffer", "after"])
def test_stream_restores_split_placeholders(make_gateway, upstream, mode) -> None:
    client = make_gateway(applications=app_yaml(stream_mode=mode))
    upstream.stream = text_stream("Kimlik ", "<TR_", "TCKN_1> kayıtlı", ", tamam.")
    response = ask(client, f"TC kimlik {TCKN}", stream=True)
    assert response.status_code == 200
    events = stream_events(response)
    assert streamed_text(events) == f"Kimlik {TCKN} kayıtlı, tamam."
    assert finish_reasons(events) == ["stop"]
    assert events[-1] == "[DONE]"
    assert all(c.get("choices") for c in events if isinstance(c, dict))  # usage chunk removed
    assert upstream.last_body["stream_options"] == {"include_usage": True}


def test_stream_masks_an_entity_split_across_chunks(make_gateway, upstream) -> None:
    client = make_gateway()
    upstream.stream = text_stream("Bize ", "ayse.de", "mir@exa", "mple.com adresinden yazın.")
    events = stream_events(ask(client, "İletişim adresi?", stream=True))
    text = streamed_text(events)
    assert EMAIL not in text and "ayse" not in text
    assert text == "Bize <EMAIL_ADDRESS_1> adresinden yazın."


def test_stream_window_releases_before_the_end(make_gateway, upstream) -> None:
    client = make_gateway(gateway_stream_holdback_chars=32, gateway_stream_step_chars=8)
    pieces = ["Bu uzun bir cevaptır ve parça parça akar. "] * 6
    upstream.stream = text_stream(*pieces)
    events = stream_events(ask(client, "Uzun yaz", stream=True))
    content_chunks = [
        c for c in events if isinstance(c, dict) and c["choices"][0]["delta"].get("content")
    ]
    assert len(content_chunks) > 1  # streamed, not buffered
    assert streamed_text(events) == "".join(pieces)


def test_stream_block_ends_with_content_filter(make_gateway, upstream) -> None:
    client = make_gateway(gateway_stream_holdback_chars=32, gateway_stream_step_chars=8)
    upstream.stream = text_stream("Önce zararsız bir cümle yazıyorum. ", f"Kart {CARD} ", "bitti.")
    events = stream_events(ask(client, "Bir kart yaz", stream=True))
    assert finish_reasons(events) == ["content_filter"]
    text = streamed_text(events)
    assert "4242" not in text


def test_stream_buffer_mode_can_still_refuse_with_an_error(make_gateway, upstream) -> None:
    client = make_gateway(applications=app_yaml(stream_mode="buffer"))
    upstream.stream = text_stream("Kart ", CARD)
    response = ask(client, "Bir kart yaz", stream=True)
    assert response.status_code == 400
    assert response.json()["error"]["umai"]["phase"] == "POST_LLM"


def test_stream_keeps_usage_when_the_caller_asks(make_gateway, upstream) -> None:
    client = make_gateway()
    events = stream_events(
        ask(client, "selam", stream=True, stream_options={"include_usage": True})
    )
    usage = [c for c in events if isinstance(c, dict) and not c.get("choices")]
    assert usage and usage[0]["usage"]["total_tokens"] == 8


def test_stream_tool_calls_are_released_whole(make_gateway, upstream) -> None:
    client = make_gateway(guardrail=AGENT_GUARDRAIL)
    first = {
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {"name": "find_customer", "arguments": '{"tckn": "<TR_'},
    }
    rest = {"index": 0, "function": {"arguments": 'TCKN_1>"}'}}
    upstream.stream = sse(
        chunk({"role": "assistant", "content": None}),
        chunk({"tool_calls": [first]}),
        chunk({"tool_calls": [rest]}),
        chunk({}, "tool_calls"),
        "[DONE]",
    )
    events = stream_events(ask(client, f"TC kimlik {TCKN} müşteri", stream=True))
    calls = [
        c["choices"][0]["delta"]["tool_calls"]
        for c in events
        if isinstance(c, dict) and c["choices"][0]["delta"].get("tool_calls")
    ]
    assert len(calls) == 1
    assert json.loads(calls[0][0]["function"]["arguments"]) == {"tckn": TCKN}
    assert finish_reasons(events) == ["tool_calls"]


def test_stream_refused_input_with_message_style(make_gateway, upstream) -> None:
    client = make_gateway(applications=app_yaml(refusal="message", refusal_text="Engellendi."))
    events = stream_events(ask(client, f"Kart {CARD}", stream=True))
    assert streamed_text(events) == "Engellendi."
    assert finish_reasons(events) == ["content_filter"]
    assert upstream.requests == []


# ------------------------------------------------------------------ the official SDK


def test_openai_sdk_works_unchanged(make_gateway, upstream) -> None:
    client = make_gateway(api_token="umai-operator-token")  # synthetic shared token
    sdk = openai.OpenAI(
        base_url="http://testserver/v1",
        api_key="umai-operator-token",
        http_client=client,
        default_headers={"X-Umai-Provider-Key": "sk-test"},
        max_retries=0,
    )
    upstream.reply = lambda body: completion("Kimlik <TR_TCKN_1> doğrulandı.")
    result = sdk.chat.completions.create(
        model="gpt-eu", messages=[{"role": "user", "content": f"TC kimlik {TCKN}"}]
    )
    assert result.choices[0].message.content == f"Kimlik {TCKN} doğrulandı."
    assert [m.id for m in sdk.models.list()] == ["gpt-eu"]

    upstream.stream = text_stream("Merhaba", " dünya")
    stream = sdk.chat.completions.create(
        model="gpt-eu", messages=[{"role": "user", "content": "selam"}], stream=True
    )
    assert "".join(c.choices[0].delta.content or "" for c in stream if c.choices) == (
        "Merhaba dünya"
    )

    with pytest.raises(openai.BadRequestError) as refused:
        sdk.chat.completions.create(
            model="gpt-eu", messages=[{"role": "user", "content": f"Kart {CARD}"}]
        )
    assert refused.value.code == "umai_blocked"


def test_applications_file_example_in_the_contract_parses() -> None:
    import re
    from pathlib import Path

    import yaml

    from umai_decision_engine.contracts.gateway import ApplicationFile

    doc = (Path(__file__).resolve().parents[2] / "docs/contracts/gateway-api.md").read_text(
        encoding="utf-8"
    )
    (block,) = re.findall(r"```yaml\n(.*?)```", doc, re.S)
    ApplicationFile.model_validate(yaml.safe_load(textwrap.dedent(block)))


def test_stream_never_releases_the_rest_of_an_entity_longer_than_the_hold_back(
    make_gateway, upstream
) -> None:
    # Hold-back 32 < a 44-character address: its local part may go out before the "@" arrives
    # (a documented limit of the hold-back), but once the address is recognised its rest never does.
    client = make_gateway(gateway_stream_holdback_chars=32, gateway_stream_step_chars=8)
    address = "very.long.mailbox.name.for.tests@example.com"
    text = f"İletişim: {address} teşekkürler, iyi günler dileriz."
    upstream.stream = text_stream(*[text[i : i + 4] for i in range(0, len(text), 4)])
    released = streamed_text(stream_events(ask(client, "İletişim?", stream=True)))
    assert address not in released
    assert "example.com" not in released
    assert released.endswith("teşekkürler, iyi günler dileriz.")


# ------------------------------------------------------------------ agents (WP5)


def _tool_call(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _agent_turn(tool_name: str, result: str) -> dict[str, Any]:
    return {
        "model": "gpt-eu",
        "messages": [
            {"role": "user", "content": "Müşteriyi bul ve özetle"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [_tool_call(tool_name, {"q": "x"})],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": result},
        ],
    }


def test_tool_call_arguments_follow_tool_call_policies(make_gateway, upstream) -> None:
    client = make_gateway()  # identity has every role, tool_call included: the value stays masked
    upstream.reply = lambda body: completion(
        None, tool_calls=[_tool_call("find_customer", {"tckn": "<TR_TCKN_1>"})]
    )
    response = ask(client, f"TC kimlik {TCKN} olan müşteriyi bul")
    arguments = response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"tckn": "<TR_TCKN_1>"}
    assert response.headers["X-Umai-Output-Decision"] == "ALLOW_WITH_MODIFICATIONS"


def test_tool_call_with_a_blocked_value_is_refused(make_gateway, upstream) -> None:
    upstream.reply = lambda body: completion(None, tool_calls=[_tool_call("pay", {"card": CARD})])
    client = make_gateway()
    response = ask(client, "Ödemeyi yap")
    assert response.status_code == 400
    assert response.json()["error"]["umai"]["phase"] == "TOOL_INPUT"
    assert CARD not in response.text

    client = make_gateway(applications=app_yaml(refusal="message"))
    choice = ask(client, "Ödemeyi yap").json()["choices"][0]
    assert choice["finish_reason"] == "content_filter"
    assert "tool_calls" not in choice["message"]


def test_stream_tool_call_with_a_blocked_value_ends_with_content_filter(
    make_gateway, upstream
) -> None:
    client = make_gateway()
    call = {
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {"name": "pay", "arguments": json.dumps({"card": CARD})},
    }
    upstream.stream = sse(
        chunk({"role": "assistant", "content": None}),
        chunk({"tool_calls": [call]}),
        chunk({}, "tool_calls"),
        "[DONE]",
    )
    response = ask(client, "Ödemeyi yap", stream=True)
    events = stream_events(response)
    assert finish_reasons(events) == ["content_filter"]
    assert "4242" not in response.text


def test_tool_results_are_checked_with_the_tools_name(make_gateway, upstream) -> None:
    client = make_gateway(guardrail=AGENT_GUARDRAIL)
    assert (
        client.post(
            "/v1/chat/completions", json=_agent_turn("crm_lookup", f"E-posta: {EMAIL}")
        ).status_code
        == 200
    )
    assert upstream.last_body["messages"][2]["content"] == f"E-posta: {EMAIL}"  # trusted tool

    assert (
        client.post(
            "/v1/chat/completions", json=_agent_turn("web_search", f"E-posta: {EMAIL}")
        ).status_code
        == 200
    )
    assert upstream.last_body["messages"][2]["content"] == "E-posta: <EMAIL_ADDRESS_1>"


def test_tool_result_with_a_blocked_value_is_refused(make_gateway, upstream) -> None:
    client = make_gateway(guardrail=AGENT_GUARDRAIL)
    response = client.post("/v1/chat/completions", json=_agent_turn("billing", f"Kart {CARD}"))
    assert response.status_code == 400
    assert response.json()["error"]["umai"]["phase"] == "TOOL_OUTPUT"
    assert upstream.requests == []


def test_tool_results_fall_back_to_the_input_check(make_gateway, upstream) -> None:
    client = make_gateway(guardrail=CHAT_ONLY_GUARDRAIL)
    response = client.post(
        "/v1/chat/completions", json=_agent_turn("crm_lookup", f"E-posta: {EMAIL}")
    )
    assert response.status_code == 200
    assert upstream.last_body["messages"][2]["content"] == "E-posta: <EMAIL_ADDRESS_1>"


# ------------------------------------------------------------------ review fixes (gateway-api 0.12)


def test_an_answer_made_of_text_parts_is_checked(make_gateway, upstream) -> None:
    client = make_gateway()

    def _parts(text: str) -> dict[str, Any]:
        answer = completion(None)
        answer["choices"][0]["message"]["content"] = [{"type": "text", "text": text}]
        return answer

    upstream.reply = lambda body: _parts(f"Yazın: {EMAIL}")
    response = ask(client, "İletişim?")
    parts = response.json()["choices"][0]["message"]["content"]
    assert parts == [{"type": "text", "text": "Yazın: <EMAIL_ADDRESS_1>"}]

    upstream.reply = lambda body: _parts(f"Kart {CARD}")
    response = ask(client, "Bir kart yaz")
    assert response.status_code == 400 and CARD not in response.text
    assert response.json()["error"]["umai"]["phase"] == "POST_LLM"


def test_legacy_function_call_and_custom_tool_calls_are_checked(make_gateway, upstream) -> None:
    client = make_gateway(guardrail=AGENT_GUARDRAIL)  # ids may go to tools, cards never
    legacy = completion(None)
    message = legacy["choices"][0]["message"]
    message["function_call"] = {"name": "pay", "arguments": json.dumps({"card": CARD})}
    upstream.reply = lambda body: legacy
    response = ask(client, "Öde")
    assert response.status_code == 400 and "4242" not in response.text
    assert response.json()["error"]["umai"]["phase"] == "TOOL_INPUT"

    message["function_call"] = {"name": "find", "arguments": '{"tckn": "<TR_TCKN_1>"}'}
    answer = ask(client, f"TC kimlik {TCKN} bul").json()["choices"][0]["message"]
    assert json.loads(answer["function_call"]["arguments"]) == {"tckn": TCKN}  # restored

    custom = {"id": "call_c", "type": "custom", "custom": {"name": "note", "input": f"kart {CARD}"}}
    upstream.reply = lambda body: completion(None, tool_calls=[custom])
    response = ask(client, "Not al")
    assert response.status_code == 400 and "4242" not in response.text

    custom["custom"]["input"] = f"yaz {EMAIL}"
    answer = ask(client, "Not al").json()["choices"][0]["message"]
    assert answer["tool_calls"][0]["custom"]["input"] == "yaz <EMAIL_ADDRESS_1>"


def test_stream_legacy_function_call_and_custom_deltas_are_held(make_gateway, upstream) -> None:
    client = make_gateway(guardrail=AGENT_GUARDRAIL)
    upstream.stream = sse(
        chunk({"role": "assistant", "content": None}),
        chunk({"function_call": {"name": "pay", "arguments": '{"card": "4242 '}}),
        chunk({"function_call": {"arguments": '4242 4242 4242"}'}}),
        chunk({}, "function_call"),
        "[DONE]",
    )
    response = ask(client, "Öde", stream=True)
    assert finish_reasons(stream_events(response)) == ["content_filter"]
    assert "4242" not in response.text

    upstream.stream = sse(
        chunk({"role": "assistant", "content": None}),
        chunk({"function_call": {"name": "find", "arguments": '{"tckn": "<TR_'}}),
        chunk({"function_call": {"arguments": 'TCKN_1>"}'}}),
        chunk({}, "function_call"),
        "[DONE]",
    )
    events = stream_events(ask(client, f"TC kimlik {TCKN} bul", stream=True))
    legacy = [
        c["choices"][0]["delta"]["function_call"]
        for c in events
        if isinstance(c, dict) and c["choices"][0]["delta"].get("function_call")
    ]
    assert len(legacy) == 1 and json.loads(legacy[0]["arguments"]) == {"tckn": TCKN}

    custom = {"index": 0, "id": "call_c", "type": "custom", "custom": {"name": "note"}}
    upstream.stream = sse(
        chunk({"role": "assistant", "content": None}),
        chunk({"tool_calls": [custom]}),
        chunk({"tool_calls": [{"index": 0, "custom": {"input": f"kart {CARD}"}}]}),
        chunk({}, "tool_calls"),
        "[DONE]",
    )
    response = ask(client, "Not al", stream=True)
    assert finish_reasons(stream_events(response)) == ["content_filter"]
    assert "4242" not in response.text


def test_numbers_in_tool_arguments_are_masked_as_json_values(make_gateway, upstream) -> None:
    client = make_gateway()  # identity is masked in tool calls too
    upstream.reply = lambda body: completion(
        None, tool_calls=[_tool_call("find_customer", {"tckn": int(TCKN)})]
    )
    response = ask(client, "Müşteriyi bul")
    arguments = response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"tckn": "<TR_TCKN_1>"}  # valid JSON, value masked

    body = _agent_turn("find_customer", "Bulundu")
    body["messages"][1]["tool_calls"][0]["function"]["arguments"] = json.dumps({"tckn": int(TCKN)})
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    sent = upstream.last_body["messages"][1]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(sent) == {"tckn": "<TR_TCKN_1>"}


def test_new_placeholders_never_reuse_one_the_history_shows(make_gateway, upstream) -> None:
    """An earlier answer showed ``<EMAIL_ADDRESS_1>`` (the model's own address, never restored);
    the caller's new address must get another placeholder, and the old one stays as it is."""

    client = make_gateway()
    upstream.reply = lambda body: completion("Önceki: <EMAIL_ADDRESS_1>, yeni: <EMAIL_ADDRESS_2>")
    body = {
        "model": "gpt-eu",
        "messages": [
            {"role": "user", "content": "Destek adresi?"},
            {"role": "assistant", "content": "Bize <EMAIL_ADDRESS_1> adresinden yazın."},
            {"role": "user", "content": f"Benimki {EMAIL}"},
        ],
    }
    response = client.post("/v1/chat/completions", json=body)
    assert upstream.last_body["messages"][2]["content"] == "Benimki <EMAIL_ADDRESS_2>"
    content = response.json()["choices"][0]["message"]["content"]
    assert content == f"Önceki: <EMAIL_ADDRESS_1>, yeni: {EMAIL}"


def test_stream_keeps_a_chunk_with_a_unicode_line_separator(make_gateway, upstream) -> None:
    client = make_gateway()
    piece = "satır\u2028ayrı"
    upstream.stream = (
        "".join(
            f"data: {json.dumps(c, ensure_ascii=False)}\n\n"
            for c in (chunk({"role": "assistant", "content": ""}), chunk({"content": piece}))
        )
        + f"data: {json.dumps(chunk({}, 'stop'))}\n\ndata: [DONE]\n\n"
    ).encode()
    response = ask(client, "Yaz", stream=True)
    events = [
        json.loads(line[len("data: ") :])
        for line in response.text.split("\n")
        if line.startswith("data: {")
    ]
    assert streamed_text(events) == piece
    assert not any("error" in e for e in events)
