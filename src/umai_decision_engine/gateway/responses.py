"""``POST /v1/responses``: the OpenAI Responses API with the same guardrails (gateway-api 0.4).

Scanned in the request: ``instructions``; ``input`` as a string or as items: ``message`` (text
parts), ``function_call`` arguments, ``function_call_output`` outputs, custom tool call input and
output. Provider-side items (reasoning, item references, hosted tool calls) pass through; any
other item that may carry content follows the route's ``unscanned_parts``.

In the answer: ``output_text`` parts (``POST_LLM``), the input of every tool item (restored, then
``TOOL_INPUT``; ``tools.responses_tool_calls``). Streams use the Responses event types
(``event:`` + ``data:`` lines, no ``[DONE]``); the gateway re-numbers ``sequence_number`` because
it holds back and rewrites text.
Shapes checked live against OpenAI on 2026-10-19.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from umai_decision_engine.contracts.common import Phase
from umai_decision_engine.contracts.evaluation import AgentAction
from umai_decision_engine.gateway import openapi
from umai_decision_engine.gateway.chat import (
    Call,
    _admit,
    _decision_headers,
    _log,
    _provider_key,
    _read_body,
    _subject,
    _target,
    admit_quota,
    call_headers,
    record_context,
    refusal_error,
    send_upstream,
)
from umai_decision_engine.gateway.content import ContentError, Segment, text_size
from umai_decision_engine.gateway.errors import GatewayHTTPError
from umai_decision_engine.gateway.fields import (
    check_responses_request,
    clean_responses_answer,
    clean_responses_upstream,
)
from umai_decision_engine.gateway.guard import CallContext, Check, strictest
from umai_decision_engine.gateway.masking import MaskSession
from umai_decision_engine.gateway.streaming import AnswerStream, ChoiceState, sse_events
from umai_decision_engine.gateway.tools import (
    RESPONSES_TOOL_ITEMS,
    ToolCall,
    input_checks,
    note_output,
    responses_tool_calls,
    responses_tool_fields,
    settle_tool_calls,
    tool_action,
)
from umai_decision_engine.gateway.upstream import forwarded_headers

router = APIRouter()

_ROLES = {"user": "user", "system": "system", "developer": "system", "assistant": "assistant"}
# Items the provider produced or keeps; they carry no caller content to scan.
_PROVIDER_ITEMS = frozenset(
    {
        "reasoning",
        "item_reference",
        "web_search_call",
        "file_search_call",
        "image_generation_call",
        "code_interpreter_call",
        "mcp_list_tools",
        "compaction",
        "compaction_trigger",
        "tool_search_call",
    }
)
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


# ------------------------------------------------------------------ request


def responses_segments(
    body: dict[str, Any],
) -> tuple[list[Segment], int, dict[int, AgentAction | None]]:
    """Segments (``message`` = item index, -1 for top-level fields), unscanned count, and the tool
    behind each tool-output item."""

    segments: list[Segment] = []
    unscanned = 0
    actions: dict[int, AgentAction | None] = {}
    instructions = body.get("instructions")
    if isinstance(instructions, str):
        if instructions:
            segments.append(Segment(-1, ("instructions",), "system", instructions))
    elif instructions is not None:
        raise ContentError("instructions must be a string")
    items = body.get("input")
    if isinstance(items, str):
        segments.append(Segment(-1, ("input",), "user", items))
        return segments, unscanned, actions
    if items is None:
        return segments, unscanned, actions
    if not isinstance(items, list):
        raise ContentError("input must be a string or an array")
    names: dict[str, str] = {}
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ContentError(f"input[{i}] must be an object")
        kind = item.get("type") or ("message" if "role" in item else None)
        if kind == "message":
            role = _ROLES.get(item.get("role"))  # type: ignore[arg-type]
            if role is None:
                raise ContentError(f"input[{i}].role is not supported")
            found, skipped = _message_parts(item.get("content"), i, role)
            segments.extend(found)
            unscanned += skipped
        elif kind in ("function_call", "custom_tool_call"):
            field = "arguments" if kind == "function_call" else "input"
            if isinstance(item.get(field), str):
                segments.append(
                    Segment(
                        i, (field,), "assistant", item[field], json_string=kind == "function_call"
                    )
                )
            if isinstance(item.get("call_id"), str) and isinstance(item.get("name"), str):
                names[item["call_id"]] = item["name"]
        elif kind in ("function_call_output", "custom_tool_call_output"):
            found, skipped = _output_parts(item.get("output"), i)
            segments.extend(found)
            unscanned += skipped
            actions[i] = tool_action(names.get(str(item.get("call_id"))))
        elif kind in _PROVIDER_ITEMS:
            continue
        else:
            unscanned += 1  # e.g. shell or computer-use outputs: not scanned as text
    return segments, unscanned, actions


def _message_parts(content: Any, index: int, role: str) -> tuple[list[Segment], int]:
    if isinstance(content, str):
        return [Segment(index, ("content",), role, content)], 0
    if content is None:
        return [], 0
    if not isinstance(content, list):
        raise ContentError(f"input[{index}].content must be a string or an array")
    found: list[Segment] = []
    unscanned = 0
    for j, part in enumerate(content):
        kind = part.get("type") if isinstance(part, dict) else None
        if kind in ("input_text", "output_text") and isinstance(part.get("text"), str):
            found.append(Segment(index, ("content", j, "text"), role, part["text"]))
        elif kind == "refusal" and isinstance(part.get("refusal"), str):
            found.append(Segment(index, ("content", j, "refusal"), role, part["refusal"]))
        else:
            unscanned += 1
    return found, unscanned


def _output_parts(output: Any, index: int) -> tuple[list[Segment], int]:
    if isinstance(output, str):
        return [Segment(index, ("output",), "user", output, tool_result=True)], 0
    if not isinstance(output, list):
        return [], 0
    found: list[Segment] = []
    unscanned = 0
    for j, part in enumerate(output):
        if (
            isinstance(part, dict)
            and part.get("type") == "input_text"
            and isinstance(part.get("text"), str)
        ):
            found.append(Segment(index, ("output", j, "text"), "user", part["text"], True))
        else:
            unscanned += 1
    return found, unscanned


def _stored_history(body: dict[str, Any]) -> bool:
    """Earlier turns the provider keeps (``previous_response_id``, a conversation): their
    placeholders are not in this call's input."""

    return bool(body.get("previous_response_id") or body.get("conversation"))


def set_body_text(body: dict[str, Any], segment: Segment, text: str) -> None:
    target: Any = body if segment.message == -1 else body["input"][segment.message]
    for key in segment.path[:-1]:
        target = target[key]
    target[segment.path[-1]] = text


# ------------------------------------------------------------------ refusals


def refusal_message_item(call: Call) -> dict[str, Any]:
    return {
        "id": f"msg_umai_{call.ctx.request_id}",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": call.route.refusal_text, "annotations": []}],
    }


def refusal_response(call: Call, model: str) -> dict[str, Any]:
    return {
        "id": f"resp_umai_{call.ctx.request_id}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "incomplete",
        "incomplete_details": {"reason": "content_filter"},
        "model": model,
        "output": [refusal_message_item(call)],
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }


def _event(event: dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def refusal_events(call: Call, model: str) -> list[dict[str, Any]]:
    """A complete, valid event sequence that carries only the refusal text."""

    response = refusal_response(call, model)
    item = response["output"][0]
    started = {**response, "status": "in_progress", "incomplete_details": None, "output": []}
    part = {"type": "output_text", "text": "", "annotations": []}
    text = call.route.refusal_text
    loc = {"item_id": item["id"], "output_index": 0, "content_index": 0}
    events = [
        {"type": "response.created", "response": started},
        {"type": "response.output_item.added", "output_index": 0, "item": {**item, "content": []}},
        {"type": "response.content_part.added", **loc, "part": part},
        {"type": "response.output_text.delta", **loc, "delta": text, "logprobs": []},
        {"type": "response.output_text.done", **loc, "text": text, "logprobs": []},
        {"type": "response.content_part.done", **loc, "part": {**part, "text": text}},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.incomplete", "response": response},
    ]
    for n, event in enumerate(events):
        event["sequence_number"] = n
    return events


def _refuse(call: Call, check: Check, *, model: str, stream: bool) -> Response:
    if call.route.refusal == "error":
        raise refusal_error(call, check)
    headers = _decision_headers(call)
    if stream:

        async def _replay() -> AsyncIterator[str]:
            for event in refusal_events(call, model):
                yield _event(event)

        return StreamingResponse(
            _replay(), media_type="text/event-stream", headers={**headers, **_SSE_HEADERS}
        )
    return JSONResponse(refusal_response(call, model), headers=headers)


# ------------------------------------------------------------------ the call


@router.post("/v1/responses", tags=["AI gateway"], **openapi.RESPONSES)
async def create_response(request: Request) -> Response:
    started = time.perf_counter()
    request_id = (request.headers.get("x-umai-request-id") or "")[:128] or f"gw-{uuid.uuid4().hex}"
    request.state.request_id = request_id
    state = request.app.state

    caller = _admit(request)
    tenant = caller.tenant
    body = await _read_body(request)
    route, fallback = _target(request, caller, body["model"])
    provider_key = _provider_key(request, route)
    try:
        segments, unscanned, actions = responses_segments(body)
    except ContentError as exc:
        raise GatewayHTTPError(400, "invalid_request", str(exc), param="input") from None
    if unscanned and route.unscanned_parts == "reject":
        raise GatewayHTTPError(
            400,
            "unsupported_content",
            "this application does not accept input the guardrail cannot scan "
            "(images, audio, files, tool outputs other than text)",
            param="input",
        )
    check_responses_request(body)
    limit = state.settings.gateway_max_input_chars
    if text_size(segments) > limit:
        raise GatewayHTTPError(
            413, "input_too_large", f"the text content exceeds {limit} characters"
        )
    admit_quota(request, caller)  # last check: a refused call never counts (ADR-0043)
    call = Call(
        ctx=CallContext(
            request_id, tenant, route, _subject(request, body), record_context(caller, route)
        ),
        provider_key=provider_key,
        session=MaskSession.for_call(
            (s.text for s in segments), stored_history=_stored_history(body)
        ),
        guard=state.guard,
        upstream=state.upstream,
        settings=state.settings,
        started=started,
        headers=call_headers(request_id, route),
        fallback=fallback,
    )
    request.state.call = call  # recorded at its end, whatever the exit (app middleware)
    if unscanned:
        call.headers["X-Umai-Unscanned-Parts"] = str(unscanned)
    stream = body.get("stream") is True
    upstream_body = copy.deepcopy(body)
    upstream_body["model"] = route.upstream.model
    clean_responses_upstream(upstream_body)
    if segments:
        results = await input_checks(call, segments, actions.get)
        worst = strictest([check for check, _ in results])
        assert worst is not None
        call.input_check = worst
        call.headers["X-Umai-Event-Id"] = results[0][0].response.event_id
        call.headers["X-Umai-Input-Decision"] = str(worst.decision.action)
        if not worst.allowed:
            _log(call, status=400 if route.refusal == "error" else 200, refused="input")
            return _refuse(call, worst, model=body["model"], stream=stream)
        for check, parts in results:
            for segment, spans in zip(parts, check.spans, strict=True):
                if spans:
                    masked = call.session.mask_text(
                        segment.text, spans, from_input=True, json_text=segment.json_string
                    )
                    set_body_text(upstream_body, segment, masked)

    if stream:
        return await _stream(call, upstream_body, client_model=body["model"])
    try:
        async with asyncio.timeout(call.remaining_s()):
            response = await send_upstream(call, upstream_body, path="responses")
    except TimeoutError:
        raise GatewayHTTPError(
            504, "upstream_timeout", "the upstream did not answer in time"
        ) from None
    raw = response.content
    passthrough = forwarded_headers(response)
    if response.status_code >= 400:
        _log(call, status=response.status_code, upstream_error=True)
        return _upstream_error(call, response, raw, passthrough)
    try:
        data = json.loads(raw)
    except ValueError:
        raise GatewayHTTPError(
            502, "upstream_invalid_response", "the upstream answer is not valid JSON"
        ) from None
    if isinstance(data, dict):
        await check_output(call, data)
        clean_responses_answer(data)
    _log(call, status=response.status_code, usage=_usage(data))
    return JSONResponse(
        data, status_code=response.status_code, headers={**passthrough, **_decision_headers(call)}
    )


def _upstream_error(
    call: Call, response: httpx.Response, raw: bytes, passthrough: dict[str, str]
) -> Response:
    return Response(
        raw,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/json"),
        headers={**passthrough, **call.headers, "X-Umai-Upstream-Error": "true"},
    )


def _usage(data: Any) -> dict[str, Any]:
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return {}
    return {
        "prompt_tokens": usage.get("input_tokens"),
        "completion_tokens": usage.get("output_tokens"),
    }


def _restore_echo(call: Call, response: dict[str, Any]) -> None:
    """The response object echoes ``instructions`` as sent upstream: give the caller theirs."""

    if call.route.restore_placeholders and isinstance(response.get("instructions"), str):
        response["instructions"] = call.session.restore(response["instructions"])


async def check_output(call: Call, data: dict[str, Any]) -> None:
    """Check, mask or refuse a non-streaming response's output, then restore placeholders."""

    route = call.route
    _restore_echo(call, data)
    output = data.get("output")
    if not isinstance(output, list):
        return
    output_enabled = call.guard.enabled(call.ctx, Phase.POST_LLM)
    blocking: Check | None = None
    tool_calls: list[ToolCall] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if not (isinstance(part, dict) and part.get("type") == "output_text"):
                    continue
                text = part.get("text")
                if not isinstance(text, str) or not text:
                    continue
                if output_enabled:
                    check = await call.guard.check(call.ctx, Phase.POST_LLM, [("assistant", text)])
                    note_output(call, check)
                    if not check.allowed:
                        blocking = blocking or check
                        continue
                    if check.masks:
                        text = call.session.mask(text, check.spans[0], from_input=False)
                part["text"] = call.session.restore(text) if route.restore_placeholders else text
        else:
            tool_calls += responses_tool_calls(item)  # masked in place
    blocking = blocking or await settle_tool_calls(call, tool_calls)
    if blocking is not None:
        if route.refusal == "error":
            raise refusal_error(call, blocking)
        data["output"] = [refusal_message_item(call)]
        data["status"] = "incomplete"
        data["incomplete_details"] = {"reason": "content_filter"}


# ------------------------------------------------------------------ streaming


# Tool-input events: the item type they belong to and the field of the done event.
_TOOL_DONE = {
    "response.function_call_arguments.done": ("function_call", "arguments"),
    "response.custom_tool_call_input.done": ("custom_tool_call", "input"),
    "response.mcp_call_arguments.done": ("mcp_call", "arguments"),
}
_TOOL_DELTA = {
    "function_call": "response.function_call_arguments.delta",
    "custom_tool_call": "response.custom_tool_call_input.delta",
    "mcp_call": "response.mcp_call_arguments.delta",
}
# Held, never passed on: the complete, checked input arrives with the item (or its done event).
_HELD = frozenset(
    {
        *_TOOL_DELTA.values(),
        "response.shell_call_command.added",
        "response.shell_call_command.delta",
        "response.shell_call_command.done",
    }
)


class ResponsesStream(AnswerStream):
    """Responses events in, checked Responses events out (text held back like ``window``).

    Text and tool input reach the caller only checked: a part or a tool call that first shows
    up in a final event (``content_part.done``, ``output_item.done``, ``response.completed``)
    is checked there."""

    def __init__(self, call: Call, *, mode: str, holdback: int, step: int) -> None:
        super().__init__(call, mode=mode, holdback=holdback, step=step)
        self.parts: dict[tuple[str, int], int] = {}  # (item_id, content_index) → state index
        self.where: dict[int, tuple[str, int, int]] = {}  # index → (item_id, output, content)
        self.settled: set[int] = set()  # parts whose whole text was checked
        self.items: dict[str, dict[str, Any]] = {}  # tool item id → the item as added
        # tool item id → (input as the upstream sent it, input as checked)
        self.checked: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self.base: dict[str, Any] = {}
        self.blocked: Check | None = None
        self.done = False

    def part(self, event: dict[str, Any]) -> ChoiceState:
        key = (str(event.get("item_id")), int(event.get("content_index") or 0))
        if key not in self.parts:
            index = len(self.parts)
            self.parts[key] = index
            self.where[index] = (key[0], int(event.get("output_index") or 0), key[1])
        return self.state(self.parts[key])

    def chunk(self, index: int, delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        item_id, output_index, content_index = self.where[index]
        return {
            "type": "response.output_text.delta",
            "item_id": item_id,
            "output_index": output_index,
            "content_index": content_index,
            "delta": delta.get("content", ""),
            "logprobs": [],
        }

    def _incomplete(self) -> list[dict[str, Any]]:
        response = {
            **self.base,
            "status": "incomplete",
            "incomplete_details": {"reason": "content_filter"},
            "output": [],
        }
        self.done = True
        return [{"type": "response.incomplete", "response": response}]

    async def _settle_part(
        self, item_id: str, content_index: int, output_index: int, text: str
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Finish an ``output_text`` part with ``text``, the upstream's full text, unless it was
        finished before: deltas still due (only for a part that was streamed) and the checked
        text, or ``None`` when the check blocks."""

        key = (item_id, content_index)
        streamed = key in self.parts
        st = self.part(
            {"item_id": item_id, "content_index": content_index, "output_index": output_index}
        )
        out: list[dict[str, Any]] = []
        if st.index not in self.settled:
            if len(text) >= len(st.raw):
                st.raw = text  # the authoritative full text
            out = await self.release(st, final=True)
            if self.mode == "after" and self.output_enabled and st.raw and not st.blocked:
                await self._check(st.raw)
            self.settled.add(st.index)
        if st.blocked:
            self.blocked = self.blocked or st.block_check
            return out, None
        return (out if streamed else []), st.sent

    async def _settle_tool_item(self, item: dict[str, Any]) -> bool:
        """Restore and check a tool item's input in place, unless that exact input was checked
        before (its done event); ``True`` when the check blocks."""

        item_id = str(item.get("id"))
        fields = responses_tool_fields(item.get("type"))
        raw = {f: copy.deepcopy(item.get(f)) for f in fields}
        seen = self.checked.get(item_id)
        if seen is not None and seen[0] == raw:
            item.update(copy.deepcopy(seen[1]))
            return False
        blocking = await settle_tool_calls(self.call, responses_tool_calls(item))
        if blocking is not None:
            self.blocked = self.blocked or blocking
            return True
        self.checked[item_id] = (raw, {f: copy.deepcopy(item.get(f)) for f in fields})
        return False

    async def _settle_item(
        self, item: dict[str, Any], output_index: int, *, emit: bool
    ) -> tuple[list[dict[str, Any]], bool]:
        """Check an output item in place; deltas still due when ``emit``, and whether the check
        blocks."""

        out: list[dict[str, Any]] = []
        if item.get("type") == "message":
            for j, part in enumerate(item.get("content") or []):
                if not (
                    isinstance(part, dict)
                    and part.get("type") == "output_text"
                    and isinstance(part.get("text"), str)
                ):
                    continue
                deltas, text = await self._settle_part(
                    str(item.get("id")), j, output_index, part["text"]
                )
                out += deltas if emit else []
                if text is None:
                    return out, True
                part["text"] = text
        elif item.get("type") in RESPONSES_TOOL_ITEMS:
            return out, await self._settle_tool_item(item)
        return out, False

    async def handle(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        if self.done:
            return []
        kind = event.get("type")
        if kind in ("response.created", "response.in_progress", "response.queued"):
            if isinstance(event.get("response"), dict):
                self.base = copy.deepcopy(event["response"])
                _restore_echo(self.call, event["response"])
            return [event]
        if kind == "response.output_text.delta":
            st = self.part(event)
            if isinstance(event.get("delta"), str):
                st.raw += event["delta"]
            out = await self.release(st, final=False)
            return self._incomplete() if st.blocked else out
        if kind == "response.output_text.done":
            text = event.get("text") if isinstance(event.get("text"), str) else ""
            out, checked = await self._settle_part(
                str(event.get("item_id")),
                int(event.get("content_index") or 0),
                int(event.get("output_index") or 0),
                text,
            )
            if checked is None:
                return out + self._incomplete()
            return [*out, {**event, "text": checked}]
        if kind in ("response.content_part.done", "response.content_part.added"):
            part = event.get("part")
            if not (isinstance(part, dict) and part.get("type") == "output_text"):
                return [event]
            if kind.endswith("added") or not isinstance(part.get("text"), str):
                return [{**event, "part": {**part, "text": ""}}]  # the text arrives checked
            out, checked = await self._settle_part(
                str(event.get("item_id")),
                int(event.get("content_index") or 0),
                int(event.get("output_index") or 0),
                part["text"],
            )
            if checked is None:
                return out + self._incomplete()
            return [*out, {**event, "part": {**part, "text": checked}}]
        if kind == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, dict):
                return await self._item_added(event, item)
            return [event]
        if kind in _HELD:
            return []  # released whole after the check
        if kind in _TOOL_DONE:
            return await self._tool_done(event)
        if kind == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict):
                out, blocked = await self._settle_item(
                    item, int(event.get("output_index") or 0), emit=True
                )
                return out + self._incomplete() if blocked else [*out, event]
            return [event]
        if kind in ("response.completed", "response.incomplete", "response.failed"):
            response = event.get("response")
            if isinstance(response, dict):
                _restore_echo(self.call, response)
                for n, item in enumerate(response.get("output") or []):
                    if isinstance(item, dict):
                        _, blocked = await self._settle_item(item, n, emit=False)
                        if blocked:
                            return self._incomplete()
            self.done = True
            return [event]
        return [event]  # reasoning summaries, hosted tool progress, errors: unchanged

    async def _item_added(
        self, event: dict[str, Any], item: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """A new output item: text it already carries is held for the checked events; a tool
        item's input, when already there, is checked now."""

        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    part["text"] = ""
        elif kind in RESPONSES_TOOL_ITEMS:
            self.items[str(item.get("id"))] = copy.deepcopy(item)
            has_input = any(item.get(f) for f in responses_tool_fields(kind))
            if has_input and await self._settle_tool_item(item):
                return self._incomplete()
        return [event]

    async def _tool_done(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """The complete input of a function, custom or MCP tool call: restored and checked,
        then released as one delta plus the done event."""

        kind, field = _TOOL_DONE[str(event.get("type"))]
        item_id = str(event.get("item_id"))
        item = copy.deepcopy(self.items.get(item_id) or {"type": kind, "id": item_id})
        item[field] = event.get(field) if isinstance(event.get(field), str) else ""
        if await self._settle_tool_item(item):
            return self._incomplete()
        loc = {"item_id": event.get("item_id"), "output_index": event.get("output_index")}
        delta = {"type": _TOOL_DELTA[kind], **loc, "delta": item[field]}
        return [delta, {**event, field: item[field]}]


class _Numbered:
    """Re-number ``sequence_number`` over everything the gateway emits."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self, event: dict[str, Any]) -> str:
        event = {**event, "sequence_number": self.n}
        event.pop("obfuscation", None)
        self.n += 1
        return _event(event)


async def _stream(call: Call, upstream_body: dict[str, Any], *, client_model: str) -> Response:
    route = call.route
    response = await send_upstream(call, upstream_body, stream=True, path="responses")
    passthrough = forwarded_headers(response)
    if response.status_code >= 400:
        try:
            raw = await response.aread()
        finally:
            await response.aclose()
        _log(call, status=response.status_code, upstream_error=True, stream=True)
        return _upstream_error(call, response, raw, passthrough)

    stream = ResponsesStream(
        call,
        mode=route.stream_mode,
        holdback=call.settings.gateway_stream_holdback_chars,
        step=call.settings.gateway_stream_step_chars,
    )
    number = _Numbered()
    usage: dict[str, Any] = {}

    async def _process(data: str) -> list[dict[str, Any]]:
        try:
            event = json.loads(data)
        except ValueError:
            return [_stream_error("upstream_invalid_response", "invalid event from upstream")]
        if not isinstance(event, dict):
            return []
        if isinstance(event.get("response"), dict) and isinstance(
            event["response"].get("usage"), dict
        ):
            usage.update(_usage(event["response"]))
        return await stream.handle(event)

    if route.stream_mode == "buffer":
        events: list[dict[str, Any]] = []
        try:
            async for data in sse_events(response):
                events.extend(await _process(data))
                if stream.done:
                    break
        except httpx.HTTPError:
            raise GatewayHTTPError(
                504, "upstream_timeout", "the upstream stream ended early"
            ) from None
        finally:
            await response.aclose()
        if stream.blocked is not None and route.refusal == "error":
            raise refusal_error(call, stream.blocked)
        _log(call, status=200, usage=usage, stream=True)

        async def _replay() -> AsyncIterator[str]:
            for event in events:
                yield number(event)

        return StreamingResponse(
            _replay(),
            media_type="text/event-stream",
            headers={**passthrough, **_decision_headers(call), **_SSE_HEADERS},
        )

    async def _events() -> AsyncIterator[str]:
        status = 200
        try:
            async for data in sse_events(response):
                for event in await _process(data):
                    yield number(event)
                if stream.done:
                    break
                if call.remaining_s() <= 0.001:
                    raise httpx.ReadTimeout("whole-call deadline")
        except httpx.HTTPError:
            status = 504
            yield number(_stream_error("upstream_timeout", "the upstream stream ended early"))
        finally:
            await response.aclose()
            _log(call, status=status, usage=usage, stream=True)

    call.streaming = True  # _events writes the call's log line and record when it closes
    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={**passthrough, **call.headers, **_SSE_HEADERS},
    )


def _stream_error(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message, "param": None}
