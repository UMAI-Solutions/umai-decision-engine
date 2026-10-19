"""``POST /v1/chat/completions`` and ``GET /v1/models`` (contract gateway-api).

One call: admission → route → input check (mask or refuse) → upstream → answer check (mask or
refuse) → restore the caller's placeholders → answer. Streaming answers go through
:mod:`umai_decision_engine.gateway.streaming`. Logs carry ids, decisions, token counts and
latencies, never content (ADR-0035).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from umai_decision_engine.contracts.common import Action, Phase
from umai_decision_engine.contracts.credentials import Scope
from umai_decision_engine.contracts.evaluation import Subject
from umai_decision_engine.contracts.gateway import Route, UmaiDecisionInfo
from umai_decision_engine.gateway.content import (
    ContentError,
    Segment,
    input_segments,
    set_text,
    text_size,
)
from umai_decision_engine.gateway.errors import GatewayHTTPError, from_admission
from umai_decision_engine.gateway.guard import CallContext, Check, Guard, strictest
from umai_decision_engine.gateway.masking import MaskSession
from umai_decision_engine.gateway.tools import (
    chat_tool_action,
    check_tool_calls,
    input_checks,
    note_output,
)
from umai_decision_engine.gateway.upstream import UpstreamClient, forwarded_headers
from umai_decision_engine.settings import Settings

router = APIRouter()
log = logging.getLogger("umai.gateway")

PROVIDER_KEY_HEADER = "x-umai-provider-key"


@dataclass
class Call:
    """Everything one gateway call carries from the request to the answer."""

    ctx: CallContext
    provider_key: str | None
    session: MaskSession
    guard: Guard
    upstream: UpstreamClient
    settings: Settings
    started: float
    headers: dict[str, str] = field(default_factory=dict)
    input_check: Check | None = None
    output_action: Action | None = None
    fallback: Route | None = None  # resolved when the call starts
    used_fallback: str | None = None

    @property
    def route(self) -> Route:
        return self.ctx.route

    def remaining_s(self) -> float:
        """Time left of the route's whole-call deadline (``timeouts_ms.total``)."""

        total = self.route.timeouts_ms.total / 1000
        return max(total - (time.perf_counter() - self.started), 0.001)

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 1)


# ------------------------------------------------------------------ admission and request


def _admit(request: Request) -> str:
    """Authenticate, rate-limit and check the ``gateway`` scope; return the call's tenant."""

    state = request.app.state
    try:
        principal = state.admission.authenticate(request.headers.get("authorization"))
        state.admission.admit(principal)
    except HTTPException as exc:
        raise from_admission(exc) from None
    if Scope.GATEWAY not in principal.scopes:
        raise GatewayHTTPError(403, "insufficient_scope", "scope gateway is required")
    # The shared operator token and the open development mode act as the default tenant.
    return principal.tenant_id or state.settings.default_tenant_id


async def _read_body(request: Request) -> dict[str, Any]:
    limit = request.app.state.settings.gateway_max_request_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise GatewayHTTPError(413, "request_too_large", f"the body exceeds {limit} bytes")
    raw = await request.body()
    if len(raw) > limit:
        raise GatewayHTTPError(413, "request_too_large", f"the body exceeds {limit} bytes")
    try:
        body = json.loads(raw)
    except ValueError:
        raise GatewayHTTPError(400, "invalid_request", "the body is not valid JSON") from None
    if not isinstance(body, dict):
        raise GatewayHTTPError(400, "invalid_request", "the body must be a JSON object")
    if not isinstance(body.get("model"), str) or not body["model"]:
        raise GatewayHTTPError(400, "invalid_request", "model is required", param="model")
    return body


def _route(request: Request, tenant: str, model: str) -> Route:
    route = request.app.state.routes.get(tenant, model)
    if route is None:
        raise GatewayHTTPError(
            404, "unknown_route", f"model {model!r} is not a route of this tenant", param="model"
        )
    return route


FALLBACK_STATUSES = frozenset({429, 500, 502, 503, 504})


def _deployment_key(settings: Settings, route: Route) -> str | None:
    if route.credential != "secret":
        return None
    secret = settings.gateway_provider_keys.get(route.secret or "")
    if secret is None:
        raise GatewayHTTPError(
            500, "route_misconfigured", "the route's provider key is not configured"
        )
    return secret.get_secret_value()


async def send_upstream(
    call: Call, body: dict[str, Any], *, stream: bool = False, path: str = "chat/completions"
) -> Any:
    """Send to the route's upstream; once to its fallback route when the first upstream is
    unreachable, times out or answers 429 / 5xx (same masked body, same guardrail)."""

    try:
        response = await call.upstream.send(
            call.route, call.provider_key, body, stream=stream, path=path
        )
        if call.fallback is None or response.status_code not in FALLBACK_STATUSES:
            return response
        await response.aclose()
        reason = str(response.status_code)
    except GatewayHTTPError as exc:
        if call.fallback is None or exc.code not in ("upstream_unreachable", "upstream_timeout"):
            raise
        reason = exc.code
    fallback = call.fallback
    call.used_fallback = fallback.route_id
    call.headers["X-Umai-Fallback"] = fallback.route_id
    log.info(
        "gateway.fallback request_id=%s route=%s fallback=%s reason=%s",
        call.ctx.request_id,
        call.route.route_id,
        fallback.route_id,
        reason,
    )
    return await call.upstream.send(
        fallback,
        _deployment_key(call.settings, fallback),
        {**body, "model": fallback.upstream.model},
        stream=stream,
        path=path,
    )


def _provider_key(request: Request, route: Route) -> str | None:
    if route.credential == "none":
        return None
    if route.credential == "pass_through":
        key = request.headers.get(PROVIDER_KEY_HEADER)
        if not key:
            raise GatewayHTTPError(
                400,
                "provider_key_required",
                "this route needs the provider key in the X-Umai-Provider-Key header",
            )
        return key
    return _deployment_key(request.app.state.settings, route)


def _subject(request: Request, body: dict[str, Any]) -> Subject:
    headers = request.headers

    def _value(name: str) -> str | None:
        value = headers.get(name)
        return value[:128] if value else None

    user = headers.get("x-umai-subject-user") or body.get("safety_identifier") or body.get("user")
    return Subject(
        user_id=str(user)[:128] if user else None,
        role=_value("x-umai-subject-role"),
        department=_value("x-umai-subject-department"),
        agent_id=_value("x-umai-agent-id"),
        session_id=_value("x-umai-session-id"),
    )


def _decision_headers(call: Call) -> dict[str, str]:
    headers = dict(call.headers)
    if call.output_action is not None:
        headers["X-Umai-Output-Decision"] = str(call.output_action)
    return headers


# ------------------------------------------------------------------ refusals


def refusal_text_completion(call: Call, model: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-umai-{call.ctx.request_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": call.route.refusal_text,
                    "refusal": None,
                },
                "finish_reason": "content_filter",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def refusal_error(call: Call, check: Check) -> GatewayHTTPError:
    decision = check.decision
    step_up = decision.action == Action.STEP_UP_APPROVAL
    info = UmaiDecisionInfo(
        request_id=call.ctx.request_id,
        event_id=check.response.event_id,
        phase=check.phase,
        action=decision.action,
        entities=[e.type for e in check.response.entities],
        matched=[m.id for m in decision.matched],
    )
    return GatewayHTTPError(
        400,
        "umai_step_up_required" if step_up else "umai_blocked",
        f"Blocked by UMAI guardrail {call.route.guardrail}: {decision.reason}",
        type_="umai_guardrail",
        headers=_decision_headers(call),
        umai=info,
    )


# ------------------------------------------------------------------ the call


@router.get("/v1/models")
async def list_models(request: Request) -> JSONResponse:
    tenant = _admit(request)
    routes = request.app.state.routes.routes(tenant)
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {"id": r.route_id, "object": "model", "created": 0, "owned_by": "umai"}
                for r in routes
            ],
        }
    )


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    started = time.perf_counter()
    request_id = (request.headers.get("x-umai-request-id") or "")[:128] or f"gw-{uuid.uuid4().hex}"
    request.state.request_id = request_id
    state = request.app.state

    tenant = _admit(request)
    body = await _read_body(request)
    route = _route(request, tenant, body["model"])
    provider_key = _provider_key(request, route)
    try:
        segments, unscanned = input_segments(body.get("messages"))
    except ContentError as exc:
        raise GatewayHTTPError(400, "invalid_request", str(exc), param="messages") from None
    if unscanned and route.unscanned_parts == "reject":
        raise GatewayHTTPError(
            400,
            "unsupported_content",
            "this route does not accept content parts the guardrail cannot scan "
            "(images, audio, files)",
            param="messages",
        )
    limit = state.settings.max_input_chars
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
        headers={"X-Umai-Request-Id": request_id},
        fallback=state.routes.get(tenant, route.fallback) if route.fallback else None,
    )
    if unscanned:
        call.headers["X-Umai-Unscanned-Parts"] = str(unscanned)
    stream = body.get("stream") is True

    upstream_body = copy.deepcopy(body)
    upstream_body["model"] = route.upstream.model
    if segments:
        results = await input_checks(call, segments, chat_tool_action(body["messages"]))
        worst = strictest([check for check, _ in results])
        assert worst is not None
        call.input_check = worst
        call.headers["X-Umai-Event-Id"] = results[0][0].response.event_id
        call.headers["X-Umai-Input-Decision"] = str(worst.decision.action)
        if not worst.allowed:
            _log(call, status=400 if route.refusal == "error" else 200, refused="input")
            return _refuse(call, worst, model=body["model"], stream=stream)
        for check, parts in results:
            if check.masks:
                _apply_input_masks(call, upstream_body["messages"], parts, check)

    if stream:
        from umai_decision_engine.gateway.streaming import stream_completion

        return await stream_completion(call, upstream_body, client_model=body["model"])
    try:
        async with asyncio.timeout(call.remaining_s()):
            response = await send_upstream(call, upstream_body)
    except TimeoutError:
        raise GatewayHTTPError(
            504, "upstream_timeout", "the upstream did not answer in time"
        ) from None
    return await _finish(call, response, client_model=body["model"])


def _apply_input_masks(
    call: Call, messages: list[Any], segments: list[Segment], check: Check
) -> None:
    for segment, spans in zip(segments, check.spans, strict=True):
        if spans:
            set_text(messages, segment, call.session.mask(segment.text, spans, from_input=True))


def _refuse(call: Call, check: Check, *, model: str, stream: bool) -> Response:
    if call.route.refusal == "error":
        raise refusal_error(call, check)
    if stream:
        from umai_decision_engine.gateway.streaming import refusal_stream

        return refusal_stream(call, model)
    return JSONResponse(refusal_text_completion(call, model), headers=_decision_headers(call))


async def _finish(call: Call, response: Any, *, client_model: str) -> Response:
    try:
        raw = await response.aread()
    finally:
        await response.aclose()
    passthrough = forwarded_headers(response)
    if response.status_code >= 400:
        _log(call, status=response.status_code, upstream_error=True)
        return Response(
            raw,
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/json"),
            headers={**passthrough, **call.headers, "X-Umai-Upstream-Error": "true"},
        )
    try:
        data = json.loads(raw)
    except ValueError:
        raise GatewayHTTPError(
            502, "upstream_invalid_response", "the upstream answer is not valid JSON"
        ) from None
    if isinstance(data, dict) and isinstance(data.get("choices"), list):
        await check_answer(call, data["choices"])
    usage = data.get("usage") if isinstance(data, dict) else None
    _log(call, status=response.status_code, usage=usage)
    return JSONResponse(
        data, status_code=response.status_code, headers={**passthrough, **_decision_headers(call)}
    )


async def check_answer(call: Call, choices: list[Any]) -> None:
    """Check, mask or refuse every choice of a non-streaming answer, then restore placeholders."""

    route = call.route
    output_enabled = call.guard.enabled(call.ctx, Phase.POST_LLM)
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if output_enabled and isinstance(content, str) and content:
            check = await call.guard.check(call.ctx, Phase.POST_LLM, [("assistant", content)])
            note_output(call, check)
            if not check.allowed:
                if route.refusal == "error":
                    raise refusal_error(call, check)
                message["content"] = route.refusal_text
                message.pop("tool_calls", None)
                choice["finish_reason"] = "content_filter"
                continue
            if check.masks:
                content = call.session.mask(content, check.spans[0], from_input=False)
        if isinstance(content, str) and route.restore_placeholders:
            content = call.session.restore(content)
        if isinstance(content, str):
            message["content"] = content
        if route.restore_placeholders:
            restore_tool_calls(call, message)
        blocking = await check_tool_calls(call, _functions(message))
        if blocking is not None:
            if route.refusal == "error":
                raise refusal_error(call, blocking)
            message["content"] = route.refusal_text
            message.pop("tool_calls", None)
            choice["finish_reason"] = "content_filter"


def _functions(message: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``function`` objects of a message's tool calls (mutable, for in-place masking)."""

    out = []
    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function") if isinstance(tool_call, dict) else None
        if isinstance(function, dict):
            out.append(function)
    return out


def restore_tool_calls(call: Call, message: dict[str, Any]) -> None:
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return
    for tool_call in calls:
        function = tool_call.get("function") if isinstance(tool_call, dict) else None
        if isinstance(function, dict) and isinstance(function.get("arguments"), str):
            function["arguments"] = call.session.restore(function["arguments"], json_string=True)


def _log(
    call: Call,
    *,
    status: int,
    usage: Any = None,
    refused: str | None = None,
    upstream_error: bool = False,
    stream: bool = False,
) -> None:
    usage = usage if isinstance(usage, dict) else {}
    log.info(
        "gateway.call request_id=%s tenant=%s route=%s upstream=%s/%s stream=%s status=%s "
        "input=%s output=%s refused=%s upstream_error=%s fallback=%s prompt_tokens=%s "
        "completion_tokens=%s total_ms=%s",
        call.ctx.request_id,
        call.ctx.tenant_id,
        call.route.route_id,
        call.route.upstream.kind,
        call.route.upstream.model,
        stream,
        status,
        call.input_check.decision.action if call.input_check else None,
        call.output_action,
        refused,
        upstream_error,
        call.used_fallback,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        call.elapsed_ms(),
    )
