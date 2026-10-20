"""``POST /v1/responses``: the OpenAI Responses API with the same guardrails (gateway-api 0.4).

Scanned in the request: ``instructions``; ``input`` as a string or as items: ``message`` (text
parts), ``function_call`` arguments, ``function_call_output`` outputs, custom tool call input and
output. Provider-side items (reasoning, item references, hosted tool calls) pass through; any
other item that may carry content follows the route's ``unscanned_parts``.

In the answer: ``output_text`` parts (``POST_LLM``), ``function_call`` arguments (restored, then
``TOOL_INPUT``). Streams use the Responses event types (``event:`` + ``data:`` lines, no
``[DONE]``); the gateway re-numbers ``sequence_number`` because it holds back and rewrites text.
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
from umai_decision_engine.gateway.chat import (
    Call,
    _admit,
    _decision_headers,
    _log,
    _provider_key,
    _read_body,
    _subject,
    _target,
    call_headers,
    refusal_error,
    send_upstream,
)
from umai_decision_engine.gateway.content import ContentError, Segment, text_size
from umai_decision_engine.gateway.errors import GatewayHTTPError
from umai_decision_engine.gateway.guard import CallContext, Check, strictest
from umai_decision_engine.gateway.masking import MaskSession
from umai_decision_engine.gateway.streaming import AnswerStream, ChoiceState, sse_events
from umai_decision_engine.gateway.tools import (
    check_tool_calls,
    input_checks,
    note_output,
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


@router.post("/v1/responses")
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
            "this gateway does not accept input the guardrail cannot scan "
            "(images, audio, files, tool outputs other than text)",
            param="input",
        )
    limit = state.settings.gateway_max_input_chars
    if text_size(segments) > limit:
        raise GatewayHTTPError(
            413, "input_too_large", f"the text content exceeds {limit} characters"
        )
    call = Call(
        ctx=CallContext(request_id, tenant, route, _subject(request, body)),
        provider_key=provider_key,
        session=MaskSession(),
        guard=state.guard,
        upstream=state.upstream,
        settings=state.settings,
        started=started,
        headers=call_headers(request_id, route),
        fallback=fallback,
    )
    if unscanned:
        call.headers["X-Umai-Unscanned-Parts"] = str(unscanned)
    stream = body.get("stream") is True
    upstream_body = copy.deepcopy(body)
    upstream_body["model"] = route.upstream.model
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
                    masked = call.session.mask(segment.text, spans, from_input=True)
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
    functions: list[dict[str, Any]] = []
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
        elif item.get("type") == "function_call" and isinstance(item.get("arguments"), str):
            if route.restore_placeholders:
                item["arguments"] = call.session.restore(item["arguments"], json_string=True)
            functions.append(item)  # has "name" and "arguments": masked in place
    blocking = blocking or await check_tool_calls(call, functions)
    if blocking is not None:
        if route.refusal == "error":
            raise refusal_error(call, blocking)
        data["output"] = [refusal_message_item(call)]
        data["status"] = "incomplete"
        data["incomplete_details"] = {"reason": "content_filter"}


# ------------------------------------------------------------------ streaming


class ResponsesStream(AnswerStream):
    """Responses events in, checked Responses events out (text held back like ``window``)."""

    def __init__(self, call: Call, *, mode: str, holdback: int, step: int) -> None:
        super().__init__(call, mode=mode, holdback=holdback, step=step)
        self.parts: dict[tuple[str, int], int] = {}  # (item_id, content_index) → state index
        self.where: dict[int, tuple[str, int, int]] = {}  # index → (item_id, output, content)
        self.calls: dict[str, dict[str, Any]] = {}  # function_call item id → {name, arguments}
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

    def _final_text(self, item_id: str, content_index: int) -> str | None:
        index = self.parts.get((item_id, content_index))
        return None if index is None else self.choices[index].sent

    def _rewrite_item(self, item: dict[str, Any]) -> None:
        if item.get("type") == "message":
            for j, part in enumerate(item.get("content") or []):
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text = self._final_text(str(item.get("id")), j)
                    if text is not None:
                        part["text"] = text
        elif item.get("type") == "function_call" and str(item.get("id")) in self.calls:
            item["arguments"] = self.calls[str(item["id"])]["arguments"]

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
            st = self.part(event)
            if isinstance(event.get("text"), str) and len(event["text"]) >= len(st.raw):
                st.raw = event["text"]  # the authoritative full text
            out = await self.release(st, final=True)
            if self.mode == "after" and self.output_enabled and st.raw and not st.blocked:
                await self._check(st.raw)
            if st.blocked:
                self.blocked = self.blocked or st.block_check
                return out + self._incomplete()
            return [*out, {**event, "text": st.sent}]
        if kind in ("response.content_part.done", "response.content_part.added"):
            part = event.get("part")
            if (
                kind.endswith("done")
                and isinstance(part, dict)
                and part.get("type") == "output_text"
            ):
                text = self._final_text(
                    str(event.get("item_id")), int(event.get("content_index") or 0)
                )
                if text is not None:
                    event = {**event, "part": {**part, "text": text}}
            return [event]
        if kind == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                self.calls[str(item.get("id"))] = {"name": item.get("name"), "arguments": ""}
            return [event]
        if kind == "response.function_call_arguments.delta":
            return []  # released whole after the check
        if kind == "response.function_call_arguments.done":
            return await self._function_done(event)
        if kind == "response.output_item.done":
            if isinstance(event.get("item"), dict):
                self._rewrite_item(event["item"])
            return [event]
        if kind in ("response.completed", "response.incomplete"):
            response = event.get("response")
            if isinstance(response, dict):
                _restore_echo(self.call, response)
                for item in response.get("output") or []:
                    if isinstance(item, dict):
                        self._rewrite_item(item)
            self.done = True
            return [event]
        return [event]  # reasoning summaries, hosted tool progress, errors: unchanged

    async def _function_done(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        item_id = str(event.get("item_id"))
        function = self.calls.setdefault(item_id, {"name": None, "arguments": ""})
        arguments = event.get("arguments") if isinstance(event.get("arguments"), str) else ""
        if self.call.route.restore_placeholders:
            arguments = self.session.restore(arguments, json_string=True)
        function["arguments"] = arguments
        blocking = await check_tool_calls(self.call, [function])
        if blocking is not None:
            self.blocked = self.blocked or blocking
            return self._incomplete()
        loc = {"item_id": event.get("item_id"), "output_index": event.get("output_index")}
        return [
            {
                "type": "response.function_call_arguments.delta",
                **loc,
                "delta": function["arguments"],
            },
            {**event, "arguments": function["arguments"]},
        ]


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

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={**passthrough, **call.headers, **_SSE_HEADERS},
    )


def _stream_error(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message, "param": None}
