"""Fields the guardrail cannot check (gateway-api 0.11) and the allow-list on every call.
Synthetic data only."""

# ruff: noqa: F811  (pytest fixtures imported from test_gateway)

from __future__ import annotations

import json
from typing import Any

from tests.integration.test_gateway import (  # noqa: F401  (fixtures)
    EMAIL,
    app_yaml,
    ask,
    chunk,
    completion,
    make_gateway,
    sse,
    upstream,
)
from tests.integration.test_gateway_responses import message_item, response_object


def _error(response) -> tuple[int, str, str | None]:
    error = response.json()["error"]
    return response.status_code, error["code"], error.get("param")


# ------------------------------------------------------------------ requests


def test_request_features_the_guardrail_cannot_check_are_refused(make_gateway, upstream) -> None:
    client = make_gateway()
    prediction = {"type": "content", "content": f"Dosya: {EMAIL}"}
    assert _error(ask(client, "kodu düzenle", prediction=prediction)) == (
        400,
        "unsupported_content",
        "prediction",
    )
    assert _error(ask(client, "oku", modalities=["text", "audio"])) == (
        400,
        "unsupported_content",
        "modalities",
    )
    body = {
        "model": "gpt-eu",
        "input": "selam",
        "prompt": {"id": "pmpt_1", "variables": {"a": EMAIL}},
    }
    response = client.post("/v1/responses", json=body)
    assert _error(response) == (400, "unsupported_content", "prompt.variables")
    assert upstream.requests == []  # nothing reached the provider


def test_unscanned_request_fields_are_not_forwarded(make_gateway, upstream) -> None:
    client = make_gateway()
    body = {
        "model": "gpt-eu",
        "messages": [{"role": "user", "content": "selam", "name": "Ahmet"}],
        "metadata": {"customer": EMAIL},
        "logprobs": True,
        "top_logprobs": 3,
    }
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    sent = upstream.last_body
    assert "metadata" not in sent and "logprobs" not in sent and "top_logprobs" not in sent
    assert sent["messages"] == [{"role": "user", "content": "selam"}]
    assert EMAIL not in json.dumps(sent)


def test_answer_text_outside_content_is_removed(make_gateway, upstream) -> None:
    client = make_gateway()

    def reply(body: dict[str, Any]) -> dict[str, Any]:
        data = completion("Kart bilgisi paylaşamam.")
        data["choices"][0]["logprobs"] = {"content": [{"token": "4242", "logprob": -0.1}]}
        data["choices"][0]["message"]["reasoning_content"] = "kart 4242 4242 4242 4242"
        return data

    upstream.reply = reply
    answer = ask(client, "selam").json()
    assert answer["choices"][0]["logprobs"] is None
    assert "reasoning_content" not in answer["choices"][0]["message"]
    assert "4242" not in json.dumps(answer)


def test_streamed_reasoning_text_is_dropped(make_gateway, upstream) -> None:
    client = make_gateway()
    upstream.stream = sse(
        chunk({"role": "assistant", "content": ""}),
        chunk({"reasoning_content": "kart 4242 4242 4242 4242"}),
        chunk({"content": "Tamam."}),
        chunk({}, "stop"),
        "[DONE]",
    )
    response = ask(client, "selam", stream=True)
    assert response.status_code == 200
    assert "4242" not in response.text and "Tamam." in response.text


def test_responses_request_and_answer_are_cleaned(make_gateway, upstream) -> None:
    client = make_gateway()
    item = message_item("Tamam.")
    item["content"][0]["logprobs"] = [{"token": "Tamam", "logprob": -0.2}]
    reasoning = {
        "id": "rs_1",
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": "x"}],
    }
    upstream.reply = lambda body: response_object([reasoning, item])
    body = {
        "model": "gpt-eu",
        "input": "selam",
        "metadata": {"customer": EMAIL},
        "top_logprobs": 2,
        "include": ["message.output_text.logprobs", "reasoning.encrypted_content"],
        "reasoning": {"effort": "low", "summary": "auto"},
    }
    answer = client.post("/v1/responses", json=body).json()
    sent = upstream.last_body
    assert "metadata" not in sent and "top_logprobs" not in sent
    assert sent["include"] == ["reasoning.encrypted_content"]
    assert sent["reasoning"] == {"effort": "low"}
    assert answer["output"][0]["summary"] == []
    assert answer["output"][1]["content"][0]["logprobs"] == []


# ------------------------------------------------------------------ allow-list


def test_the_built_in_provider_obeys_the_allow_list(make_gateway, upstream) -> None:
    allow = ["https://models.example.test"]
    client = make_gateway(gateway_upstream_allowlist=allow)
    assert _error(ask(client, "selam")) == (403, "provider_not_allowed", "model")
    # without aliases or models the list comes from the provider, which may not be called
    client = make_gateway(gateway_upstream_allowlist=allow, applications=app_yaml(aliases=[]))
    assert client.get("/v1/models").status_code == 403
    assert upstream.requests == []
