"""UMAI gateway: the OpenAI Responses API surface against a fake upstream (gateway-api 0.4).

Event and object shapes follow a live recording of the OpenAI Responses API (2026-10-19).
Synthetic content only (AGENTS rule 2).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from tests.integration.test_gateway import (  # noqa: F401  (fixtures)
    AGENT_GUARDRAIL,
    CARD,
    EMAIL,
    TCKN,
    make_gateway,
    route_yaml,
    upstream,
)


def response_object(output: list[dict[str, Any]], status: str = "completed") -> dict[str, Any]:
    return {
        "id": "resp_up",
        "object": "response",
        "created_at": 1792442629,
        "status": status,
        "incomplete_details": None,
        "instructions": None,
        "model": "gpt-x",
        "output": output,
        "usage": {"input_tokens": 9, "output_tokens": 4, "total_tokens": 13},
    }


def message_item(text: str, item_id: str = "msg_1") -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "annotations": [], "logprobs": [], "text": text}],
    }


def function_item(arguments: str, item_id: str = "fc_1") -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "function_call",
        "status": "completed",
        "arguments": arguments,
        "call_id": "call_1",
        "name": "find_customer",
    }


def sse_events(*events: dict[str, Any]) -> bytes:
    lines = []
    for n, event in enumerate(events):
        event = {**event, "sequence_number": n}
        lines.append(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n")
    return "".join(lines).encode()


def text_events(*pieces: str, item_id: str = "msg_1") -> bytes:
    full = "".join(pieces)
    loc = {"item_id": item_id, "output_index": 0, "content_index": 0}
    started = response_object([], status="in_progress")
    return sse_events(
        {"type": "response.created", "response": started},
        {"type": "response.in_progress", "response": started},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**message_item("", item_id), "status": "in_progress", "content": []},
        },
        {
            "type": "response.content_part.added",
            **loc,
            "part": {"type": "output_text", "annotations": [], "logprobs": [], "text": ""},
        },
        *(
            {
                "type": "response.output_text.delta",
                **loc,
                "delta": p,
                "logprobs": [],
                "obfuscation": "x",
            }
            for p in pieces
        ),
        {"type": "response.output_text.done", **loc, "text": full, "logprobs": []},
        {
            "type": "response.content_part.done",
            **loc,
            "part": {"type": "output_text", "annotations": [], "logprobs": [], "text": full},
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": message_item(full, item_id),
        },
        {"type": "response.completed", "response": response_object([message_item(full, item_id)])},
    )


def function_events(*pieces: str) -> bytes:
    full = "".join(pieces)
    loc = {"item_id": "fc_1", "output_index": 0}
    return sse_events(
        {"type": "response.created", "response": response_object([], status="in_progress")},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**function_item(""), "status": "in_progress"},
        },
        *({"type": "response.function_call_arguments.delta", **loc, "delta": p} for p in pieces),
        {"type": "response.function_call_arguments.done", **loc, "arguments": full},
        {"type": "response.output_item.done", "output_index": 0, "item": function_item(full)},
        {"type": "response.completed", "response": response_object([function_item(full)])},
    )


def read_events(response: httpx.Response) -> list[dict[str, Any]]:
    events = []
    lines = response.text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("data: "):
            event = json.loads(line[len("data: ") :])
            assert lines[i - 1] == f"event: {event['type']}"
            events.append(event)
    return events


def delta_text(events: list[dict[str, Any]]) -> str:
    return "".join(e["delta"] for e in events if e["type"] == "response.output_text.delta")


def post(client: TestClient, **body: Any) -> httpx.Response:
    return client.post("/v1/responses", json={"model": "gpt-eu", **body})


@pytest.fixture
def responses_upstream(upstream):  # noqa: F811
    upstream.reply = lambda body: response_object([message_item("Tamam.")])
    upstream.stream = text_events("Tamam.")
    return upstream


# ------------------------------------------------------------------ non-streaming


def test_string_input_is_masked_and_the_answer_restored(make_gateway, responses_upstream) -> None:  # noqa: F811
    client = make_gateway()
    responses_upstream.reply = lambda body: response_object(
        [message_item("Kimlik <TR_TCKN_1> tamam.")]
    )
    response = post(client, input=f"TC kimlik {TCKN} kontrol et", instructions=f"Müşteri: {EMAIL}")
    assert response.status_code == 200
    request = responses_upstream.requests[-1]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    sent = json.loads(request.content)
    assert sent["model"] == "gpt-x"
    assert sent["input"] == "TC kimlik <TR_TCKN_1> kontrol et"
    assert sent["instructions"] == "Müşteri: <EMAIL_ADDRESS_1>"
    body = response.json()
    assert body["output"][0]["content"][0]["text"] == f"Kimlik {TCKN} tamam."
    assert response.headers["X-Umai-Input-Decision"] == "ALLOW_WITH_MODIFICATIONS"


def test_input_items_and_tool_results(make_gateway, responses_upstream) -> None:  # noqa: F811
    client = make_gateway(guardrail=AGENT_GUARDRAIL)
    items = [
        {"role": "user", "content": [{"type": "input_text", "text": f"TC kimlik {TCKN} ara"}]},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "crm_lookup",
            "arguments": json.dumps({"tckn": TCKN}),
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": f"Bulundu, e-posta {EMAIL}",
        },
        {"type": "reasoning", "id": "rs_1", "summary": []},
    ]
    assert post(client, input=items).status_code == 200
    sent = json.loads(responses_upstream.requests[-1].content)["input"]
    assert sent[0]["content"][0]["text"] == "TC kimlik <TR_TCKN_1> ara"
    assert json.loads(sent[1]["arguments"]) == {"tckn": "<TR_TCKN_1>"}
    assert sent[2]["output"] == f"Bulundu, e-posta {EMAIL}"  # trusted tool (exception on its name)
    assert sent[3] == items[3]  # provider items pass through


def test_unscannable_items_follow_the_route(make_gateway, responses_upstream) -> None:  # noqa: F811
    items = [{"type": "local_shell_call_output", "call_id": "c", "output": "ls"}]
    client = make_gateway()
    response = post(client, input=items)
    assert response.status_code == 400 and response.json()["error"]["code"] == "unsupported_content"
    client = make_gateway(routes=route_yaml(unscanned_parts="pass_unscanned"))
    assert post(client, input=items).status_code == 200


def test_blocked_input_and_answer(make_gateway, responses_upstream) -> None:  # noqa: F811
    client = make_gateway()
    response = post(client, input=f"Kart {CARD}")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "umai_blocked"
    assert responses_upstream.requests == []

    responses_upstream.reply = lambda body: response_object([message_item(f"Kart: {CARD}")])
    response = post(client, input="Bir kart yaz")
    assert response.status_code == 400
    assert response.json()["error"]["umai"]["phase"] == "POST_LLM"

    client = make_gateway(routes=route_yaml(refusal="message", refusal_text="Engellendi."))
    body = post(client, input="Bir kart yaz").json()
    assert body["status"] == "incomplete"
    assert body["incomplete_details"] == {"reason": "content_filter"}
    assert body["output"][0]["content"][0]["text"] == "Engellendi."
    assert CARD not in json.dumps(body)


def test_function_call_arguments(make_gateway, responses_upstream) -> None:  # noqa: F811
    responses_upstream.reply = lambda body: response_object(
        [function_item(json.dumps({"tckn": "<TR_TCKN_1>"}))]
    )
    client = make_gateway(guardrail=AGENT_GUARDRAIL)  # agents may send ids to their tools
    out = post(client, input=f"TC kimlik {TCKN} bul").json()["output"][0]
    assert json.loads(out["arguments"]) == {"tckn": TCKN}

    client = make_gateway()  # identity masked for tool calls too
    out = post(client, input=f"TC kimlik {TCKN} bul").json()["output"][0]
    assert json.loads(out["arguments"]) == {"tckn": "<TR_TCKN_1>"}


# ------------------------------------------------------------------ streaming


@pytest.mark.parametrize("mode", ["window", "buffer", "after"])
def test_stream_restores_split_placeholders(make_gateway, responses_upstream, mode) -> None:  # noqa: F811
    client = make_gateway(routes=route_yaml(stream_mode=mode))
    responses_upstream.stream = text_events("Kimlik ", "<TR_", "TCKN_1> kayıtlı.")
    events = read_events(post(client, input=f"TC kimlik {TCKN}", stream=True))
    expected = f"Kimlik {TCKN} kayıtlı."
    assert delta_text(events) == expected
    done = [e for e in events if e["type"] == "response.output_text.done"]
    assert done[0]["text"] == expected
    final = events[-1]
    assert final["type"] == "response.completed"
    assert final["response"]["output"][0]["content"][0]["text"] == expected
    assert [e["sequence_number"] for e in events] == list(range(len(events)))
    assert all("obfuscation" not in e for e in events)


def test_stream_masks_an_entity_split_across_deltas(make_gateway, responses_upstream) -> None:  # noqa: F811
    client = make_gateway()
    responses_upstream.stream = text_events("Bize ", "ayse.de", "mir@exa", "mple.com yazın.")
    events = read_events(post(client, input="İletişim?", stream=True))
    assert delta_text(events) == "Bize <EMAIL_ADDRESS_1> yazın."
    assert EMAIL not in json.dumps(events)


def test_stream_block_ends_incomplete(make_gateway, responses_upstream) -> None:  # noqa: F811
    client = make_gateway(gateway_stream_holdback_chars=32, gateway_stream_step_chars=8)
    responses_upstream.stream = text_events(
        "Önce zararsız bir cümle yazıyorum. ", f"Kart {CARD} ", "bitti."
    )
    response = post(client, input="Bir kart yaz", stream=True)
    events = read_events(response)
    assert events[-1]["type"] == "response.incomplete"
    assert events[-1]["response"]["incomplete_details"] == {"reason": "content_filter"}
    assert "4242" not in response.text


def test_stream_buffer_mode_can_refuse_with_an_error(make_gateway, responses_upstream) -> None:  # noqa: F811
    client = make_gateway(routes=route_yaml(stream_mode="buffer"))
    responses_upstream.stream = text_events("Kart ", CARD)
    response = post(client, input="Bir kart yaz", stream=True)
    assert response.status_code == 400


def test_stream_function_call_is_released_whole(make_gateway, responses_upstream) -> None:  # noqa: F811
    client = make_gateway(guardrail=AGENT_GUARDRAIL)
    responses_upstream.stream = function_events('{"tckn": "<TR_', 'TCKN_1>"}')
    events = read_events(post(client, input=f"TC kimlik {TCKN} bul", stream=True))
    deltas = [e for e in events if e["type"] == "response.function_call_arguments.delta"]
    assert len(deltas) == 1 and json.loads(deltas[0]["delta"]) == {"tckn": TCKN}
    item = next(e for e in events if e["type"] == "response.output_item.done")["item"]
    assert json.loads(item["arguments"]) == {"tckn": TCKN}
    assert json.loads(events[-1]["response"]["output"][0]["arguments"]) == {"tckn": TCKN}


def test_stream_refused_input_with_message_style(make_gateway, responses_upstream) -> None:  # noqa: F811
    client = make_gateway(routes=route_yaml(refusal="message", refusal_text="Engellendi."))
    events = read_events(post(client, input=f"Kart {CARD}", stream=True))
    assert delta_text(events) == "Engellendi."
    assert events[-1]["type"] == "response.incomplete"
    assert responses_upstream.requests == []


# ------------------------------------------------------------------ the official SDK


def test_openai_sdk_responses(make_gateway, responses_upstream) -> None:  # noqa: F811
    client = make_gateway(api_token="umai-operator-token")
    sdk = openai.OpenAI(
        base_url="http://testserver/v1",
        api_key="umai-operator-token",
        http_client=client,
        default_headers={"X-Umai-Provider-Key": "sk-test"},
        max_retries=0,
    )
    responses_upstream.reply = lambda body: response_object([message_item("Kimlik <TR_TCKN_1>.")])
    result = sdk.responses.create(model="gpt-eu", input=f"TC kimlik {TCKN}")
    assert result.output_text == f"Kimlik {TCKN}."

    responses_upstream.stream = text_events("Kimlik ", "<TR_TCKN_1>", ".")
    stream = sdk.responses.create(model="gpt-eu", input=f"TC kimlik {TCKN}", stream=True)
    text = "".join(e.delta for e in stream if e.type == "response.output_text.delta")
    assert text == f"Kimlik {TCKN}."

    with pytest.raises(openai.BadRequestError) as refused:
        sdk.responses.create(model="gpt-eu", input=f"Kart {CARD}")
    assert refused.value.code == "umai_blocked"
