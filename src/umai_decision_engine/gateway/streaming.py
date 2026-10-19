"""Streaming answers (contract gateway-api, Streaming).

Modes, per route:

- ``buffer``: the whole upstream stream is read and checked before the caller gets a response,
  so a blocked answer can still be an HTTP error; then the checked answer is streamed.
- ``window``: text is released as it arrives, minus a hold-back of the last *k* characters per
  choice. Every release step re-checks the raw tail (L0 spans, decision); an entity or a
  placeholder that straddles the hold-back boundary is held until it is complete. A ``BLOCK``
  ends the choice with ``finish_reason: content_filter``.
- ``after``: text streams unchanged (placeholders still restored); the answer is checked at the
  end and the decision recorded. Detection only.

Tool-call deltas are buffered per call and released complete when the choice finishes. Chunks
keep the provider's shape; the gateway only writes ``delta.content``, ``delta.tool_calls`` and
``finish_reason``. A usage-only chunk the caller did not ask for is removed.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi.responses import Response, StreamingResponse

from umai_decision_engine.contracts.common import Phase
from umai_decision_engine.gateway.chat import (
    Call,
    _decision_headers,
    _log,
    refusal_error,
    send_upstream,
)
from umai_decision_engine.gateway.errors import GatewayHTTPError
from umai_decision_engine.gateway.guard import Check
from umai_decision_engine.gateway.masking import MaskSession, Span
from umai_decision_engine.gateway.tools import check_tool_calls, note_output
from umai_decision_engine.gateway.upstream import forwarded_headers

log = logging.getLogger("umai.gateway.stream")

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(data: dict[str, Any] | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"data: {payload}\n\n"


async def sse_events(response: httpx.Response) -> AsyncIterator[str]:
    """The ``data`` of each server-sent event (multi-line data joined with newlines)."""

    data: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data:
                yield "\n".join(data)
                data = []
            continue
        if line.startswith(":"):
            continue  # comment / keep-alive
        name, _, value = line.partition(":")
        if name == "data":
            data.append(value[1:] if value.startswith(" ") else value)
    if data:
        yield "\n".join(data)


# ------------------------------------------------------------------ per-choice state


@dataclass
class ToolCallBuffer:
    id: str | None = None
    type: str | None = None
    name: str = ""
    arguments: str = ""

    def as_delta(self, index: int) -> dict[str, Any]:
        delta: dict[str, Any] = {"index": index, "function": {"name": self.name}}
        if self.id is not None:
            delta["id"] = self.id
        delta["type"] = self.type or "function"
        delta["function"]["arguments"] = self.arguments
        return delta


@dataclass
class ChoiceState:
    index: int
    raw: str = ""  # everything the upstream sent for this choice
    released: int = 0  # raw characters already sent (after masking and restoration)
    sent: str = ""  # what the caller received for this choice (masked, restored)
    tool_calls: dict[int, ToolCallBuffer] = field(default_factory=dict)
    announced: bool = False  # the first delta (role) went out
    finished: bool = False
    blocked: bool = False
    block_check: Check | None = None


class AnswerStream:
    """Turns upstream chunks into checked chunks for one call."""

    def __init__(self, call: Call, *, mode: str, holdback: int, step: int) -> None:
        self.call = call
        self.mode = mode
        self.holdback = holdback
        self.step = step
        self.output_enabled = call.guard.enabled(call.ctx, Phase.POST_LLM)
        self.choices: dict[int, ChoiceState] = {}
        self.template: dict[str, Any] = {}

    @property
    def session(self) -> MaskSession:
        return self.call.session

    def state(self, index: int) -> ChoiceState:
        if index not in self.choices:
            self.choices[index] = ChoiceState(index)
        return self.choices[index]

    def chunk(self, index: int, delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        base = {k: v for k, v in self.template.items() if k not in ("choices", "usage")}
        base.setdefault("object", "chat.completion.chunk")
        base["choices"] = [{"index": index, "delta": delta, "finish_reason": finish}]
        return base

    # -------------------------------------------------------------- checks

    async def _check(self, text: str) -> Check:
        check = await self.call.guard.check(self.call.ctx, Phase.POST_LLM, [("assistant", text)])
        note_output(self.call, check)
        return check

    def _render(self, raw_piece: str, spans: list[Span]) -> str:
        """Mask new entities in a raw piece, then put the caller's own values back."""

        text = self.session.mask(raw_piece, spans, from_input=False) if spans else raw_piece
        if self.call.route.restore_placeholders:
            text = self.session.restore(text)
        return text

    def _safe_end(self, st: ChoiceState, end: int) -> int:
        """Move ``end`` back so a placeholder the model is still writing is not split."""

        if self.call.route.restore_placeholders:
            end -= MaskSession.unfinished_tail(st.raw[:end])
        return max(end, st.released)

    # -------------------------------------------------------------- release

    async def release(self, st: ChoiceState, *, final: bool) -> list[dict[str, Any]]:
        """Chunks for the text of ``st`` that may go out now."""

        if st.blocked:
            return []
        if self.mode == "buffer" and not final:
            return []
        if self.mode == "after" or not self.output_enabled:
            end = len(st.raw) if final else self._safe_end(st, len(st.raw))
            return self._emit(st, end, [])
        if not final and len(st.raw) - st.released < self.holdback + self.step:
            return []
        # window (and buffer at the end): check the raw tail, hold what straddles the boundary
        scan_from = 0 if final else max(0, st.released - self.holdback)
        check = await self._check(st.raw[scan_from:])
        if not check.allowed:
            st.blocked = True
            st.block_check = check
            return []
        spans = [(s + scan_from, e + scan_from, t, h) for s, e, t, h in check.spans[0]]
        end = len(st.raw) if final else self._safe_end(st, len(st.raw) - self.holdback)
        for start, stop, _, _ in spans:
            if start < st.released < stop:
                # An entity longer than the hold-back whose start already went out: never
                # release its rest (it is still arriving → hold; complete → drop it).
                if stop > end:
                    return []
                st.released = stop
        for start, stop, _, _ in spans:
            if start < end < stop:
                end = start  # an entity straddles the boundary: hold it whole
        return self._emit(st, end, spans)

    def _emit(self, st: ChoiceState, end: int, spans: list[Span]) -> list[dict[str, Any]]:
        if end <= st.released:
            return []
        piece = st.raw[st.released : end]
        local = [
            (s - st.released, e - st.released, t, h)
            for s, e, t, h in spans
            if s >= st.released and e <= end
        ]
        st.released = end
        text = self._render(piece, local)
        st.sent += text
        return [self.chunk(st.index, {"content": text})] if text else []

    def finish_chunks(self, st: ChoiceState, finish_reason: str | None) -> list[dict[str, Any]]:
        if st.blocked:
            return [self.chunk(st.index, {}, "content_filter")]
        chunks: list[dict[str, Any]] = []
        if st.tool_calls:
            deltas = [st.tool_calls[k].as_delta(k) for k in sorted(st.tool_calls)]
            chunks.append(self.chunk(st.index, {"tool_calls": deltas}))
        chunks.append(self.chunk(st.index, {}, finish_reason or "stop"))
        return chunks

    # -------------------------------------------------------------- one upstream chunk

    async def feed(self, chunk: dict[str, Any]) -> list[dict[str, Any]]:
        self.template = chunk
        out: list[dict[str, Any]] = []
        for choice in chunk.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            st = self.state(int(choice.get("index") or 0))
            if st.finished or st.blocked:
                continue
            delta = choice.get("delta") or {}
            # Role announcement, model refusal text and other fields pass through unchanged.
            extra = {
                k: v
                for k, v in delta.items()
                if k not in ("content", "tool_calls") and (v is not None or not st.announced)
            }
            if extra:
                out.append(self.chunk(st.index, extra))
                st.announced = True
            if isinstance(delta.get("content"), str):
                st.raw += delta["content"]
            for tool_delta in delta.get("tool_calls") or []:
                _merge_tool_delta(st, tool_delta)
            finish = choice.get("finish_reason")
            if finish is not None:
                out.extend(await self.finish(st, finish))
            else:
                out.extend(await self.release(st, final=False))
                if st.blocked:
                    out.extend(self.finish_chunks(st, None))
                    st.finished = True
        return out

    async def finish(self, st: ChoiceState, finish_reason: str) -> list[dict[str, Any]]:
        """Final release of a choice: the whole answer is checked (``after``: recorded only,
        nothing can be recalled), then tool calls and the finish chunk go out."""

        out = await self.release(st, final=True)
        if self.mode == "after" and self.output_enabled and st.raw and not st.blocked:
            await self._check(st.raw)
        if st.tool_calls and not st.blocked:
            await self._check_tool_calls(st)
        out.extend(self.finish_chunks(st, finish_reason))
        st.finished = True
        return out

    async def _check_tool_calls(self, st: ChoiceState) -> None:
        """Restore the caller's values in each complete call, then check it (TOOL_INPUT)."""

        buffers = [st.tool_calls[k] for k in sorted(st.tool_calls)]
        functions = []
        for buffer in buffers:
            if self.call.route.restore_placeholders:
                buffer.arguments = self.session.restore(buffer.arguments, json_string=True)
            functions.append({"name": buffer.name, "arguments": buffer.arguments})
        blocking = await check_tool_calls(self.call, functions)
        if blocking is not None:
            st.blocked = True
            st.block_check = blocking
            return
        for buffer, function in zip(buffers, functions, strict=True):
            buffer.arguments = function["arguments"]

    async def close_unfinished(self) -> list[dict[str, Any]]:
        """Choices the upstream ended without a finish reason get the same final check."""

        out: list[dict[str, Any]] = []
        for st in self.choices.values():
            if not st.finished:
                out.extend(await self.finish(st, "stop"))
        return out

    def blocked_everywhere(self) -> bool:
        return bool(self.choices) and all(s.finished and s.blocked for s in self.choices.values())

    def all_done(self) -> bool:
        return bool(self.choices) and all(s.finished for s in self.choices.values())


def _merge_tool_delta(st: ChoiceState, delta: Any) -> None:
    if not isinstance(delta, dict):
        return
    index = int(delta.get("index") or 0)
    buffer = st.tool_calls.setdefault(index, ToolCallBuffer())
    if delta.get("id"):
        buffer.id = delta["id"]
    if delta.get("type"):
        buffer.type = delta["type"]
    function = delta.get("function") or {}
    if isinstance(function.get("name"), str):
        buffer.name += function["name"]
    if isinstance(function.get("arguments"), str):
        buffer.arguments += function["arguments"]


# ------------------------------------------------------------------ entry points


def refusal_stream(call: Call, model: str) -> StreamingResponse:
    """``refusal: message`` for a streaming request whose input was refused."""

    template = {
        "id": f"chatcmpl-umai-{call.ctx.request_id}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }

    async def _events() -> AsyncIterator[str]:
        first = {
            **template,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": call.route.refusal_text},
                    "finish_reason": None,
                }
            ],
        }
        yield _sse(first)
        yield _sse(
            {**template, "choices": [{"index": 0, "delta": {}, "finish_reason": "content_filter"}]}
        )
        yield _sse("[DONE]")

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={**_decision_headers(call), **_SSE_HEADERS},
    )


async def stream_completion(
    call: Call, upstream_body: dict[str, Any], *, client_model: str
) -> Response:
    route = call.route
    options = upstream_body.get("stream_options")
    wants_usage = isinstance(options, dict) and options.get("include_usage") is True
    added_usage = False
    if not wants_usage and route.upstream.kind in ("openai", "azure_openai"):
        upstream_body["stream_options"] = {**(options or {}), "include_usage": True}
        added_usage = True

    response = await send_upstream(call, upstream_body, stream=True)
    passthrough = forwarded_headers(response)
    if response.status_code >= 400:
        try:
            raw = await response.aread()
        finally:
            await response.aclose()
        _log(call, status=response.status_code, upstream_error=True, stream=True)
        return Response(
            raw,
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/json"),
            headers={**passthrough, **call.headers, "X-Umai-Upstream-Error": "true"},
        )

    answer = AnswerStream(
        call,
        mode=route.stream_mode,
        holdback=call.settings.gateway_stream_holdback_chars,
        step=call.settings.gateway_stream_step_chars,
    )
    usage: dict[str, Any] = {}

    async def _process(event: str) -> list[str]:
        if event == "[DONE]":
            return []
        try:
            chunk = json.loads(event)
        except ValueError:
            return [_sse(_stream_error("upstream_invalid_response", "invalid chunk from upstream"))]
        if not isinstance(chunk, dict):
            return []
        if "error" in chunk and not chunk.get("choices"):
            return [_sse(chunk)]  # a provider error mid-stream passes through
        if not chunk.get("choices"):
            if isinstance(chunk.get("usage"), dict):
                usage.update(chunk["usage"])
                if added_usage:
                    return []
            return [_sse(chunk)]
        return [_sse(c) for c in await answer.feed(chunk)]

    if route.stream_mode == "buffer":
        # Read and check everything first: the response status can still say "refused".
        lines: list[str] = []
        try:
            async for event in sse_events(response):
                lines.extend(await _process(event))
                if call.remaining_s() <= 0.001:
                    raise httpx.ReadTimeout("whole-call deadline")
        except httpx.HTTPError:
            await response.aclose()
            raise GatewayHTTPError(
                504, "upstream_timeout", "the upstream stream ended early"
            ) from None
        finally:
            await response.aclose()
        lines.extend(_sse(c) for c in await answer.close_unfinished())
        blocked = [s for s in answer.choices.values() if s.block_check is not None]
        if blocked and route.refusal == "error":
            raise refusal_error(call, blocked[0].block_check)  # type: ignore[arg-type]
        _log(call, status=200, usage=usage, stream=True)

        async def _replay() -> AsyncIterator[str]:
            for line in lines:
                yield line
            yield _sse("[DONE]")

        return StreamingResponse(
            _replay(),
            media_type="text/event-stream",
            headers={**passthrough, **_decision_headers(call), **_SSE_HEADERS},
        )

    async def _events() -> AsyncIterator[str]:
        status = 200
        try:
            async for event in sse_events(response):
                for line in await _process(event):
                    yield line
                if answer.blocked_everywhere():
                    break  # stop paying for tokens nobody will see
                if call.remaining_s() <= 0.001:
                    raise httpx.ReadTimeout("whole-call deadline")
            for chunk in await answer.close_unfinished():
                yield _sse(chunk)
        except httpx.HTTPError:
            status = 504
            yield _sse(_stream_error("upstream_timeout", "the upstream stream ended early"))
        finally:
            await response.aclose()
            _log(call, status=status, usage=usage, stream=True)
        yield _sse("[DONE]")

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={**passthrough, **call.headers, **_SSE_HEADERS},
    )


def _stream_error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": "umai_gateway", "param": None, "code": code}}
