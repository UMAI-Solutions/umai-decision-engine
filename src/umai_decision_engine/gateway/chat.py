"""``POST /v1/chat/completions`` and ``GET /v1/models`` (contract gateway-api).

One call: admission → gateway and model → input check (mask or refuse) → upstream → answer
check (mask or refuse) → restore the caller's placeholders → answer. Streaming answers go through
:mod:`umai_decision_engine.gateway.streaming`. Logs carry ids, decisions, token counts and
latencies, never content (ADR-0035).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from umai_decision_engine.contracts.common import Action, Phase
from umai_decision_engine.contracts.credentials import Scope
from umai_decision_engine.contracts.evaluation import Subject
from umai_decision_engine.contracts.events import RecordContext
from umai_decision_engine.contracts.gateway import (
    APPLICATION_ID,
    MODEL_REF,
    Application,
    ResolveError,
    Route,
    UmaiDecisionInfo,
)
from umai_decision_engine.gateway.content import (
    ContentError,
    Segment,
    input_segments,
    set_text,
    text_size,
)
from umai_decision_engine.gateway.errors import GatewayHTTPError, from_admission
from umai_decision_engine.gateway.fields import (
    check_chat_request,
    clean_chat_answer,
    clean_chat_upstream,
)
from umai_decision_engine.gateway.guard import BLOCKING, CallContext, Check, Guard, strictest
from umai_decision_engine.gateway.masking import MaskSession
from umai_decision_engine.gateway.tools import (
    chat_tool_action,
    chat_tool_calls,
    input_checks,
    note_output,
    settle_tool_calls,
)
from umai_decision_engine.gateway.upstream import UpstreamClient, forwarded_headers
from umai_decision_engine.settings import Settings

router = APIRouter()
log = logging.getLogger("umai.gateway")

PROVIDER_KEY_HEADER = "x-umai-provider-key"
APPLICATION_HEADER = "x-umai-application"
_APPLICATION_ID = re.compile(APPLICATION_ID)
_MODEL_REF = re.compile(MODEL_REF)


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
    finished: bool = False  # its gateway.call line and usage record are written
    streaming: bool = False  # a stream records itself when it closes

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


@dataclass(frozen=True)
class Caller:
    """Who is calling: the tenant, the application the key belongs to (ADR-0039), and the key
    itself for the records (ADR-0041)."""

    tenant: str
    application_id: str | None = None
    key_id: str | None = None
    environment: str | None = None


def record_context(caller: Caller, route: Route) -> RecordContext:
    """The records' view of a call: a server-made call id (a caller's request id may repeat),
    the key, and the application the call runs as."""

    return RecordContext(
        source="gateway",
        key_id=caller.key_id,
        environment=caller.environment if caller.environment in ("live", "test") else None,
        application_id=route.route_id,
        gateway_call_id=f"gc-{uuid.uuid4().hex}",
    )


def _admit(request: Request) -> Caller:
    """Authenticate, rate-limit and check the ``gateway`` scope; return the caller."""

    state = request.app.state
    try:
        principal = state.admission.authenticate(request.headers.get("authorization"))
        state.admission.admit(principal)
    except HTTPException as exc:
        raise from_admission(exc) from None
    if Scope.GATEWAY not in principal.scopes:
        raise GatewayHTTPError(403, "insufficient_scope", "scope gateway is required")
    # The shared operator token and the open development mode act as the default tenant.
    tenant = principal.tenant_id or state.settings.default_tenant_id
    return Caller(tenant, principal.application_id, principal.key_id, principal.env)


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


def _application(request: Request, caller: Caller) -> Application:
    """The call's application: the key's, else ``X-Umai-Application``, else the tenant's only one
    (gateway-api 0.10, ADR-0039 §4). A key never moves to another application."""

    table = request.app.state.applications
    named = (request.headers.get(APPLICATION_HEADER) or "").strip() or None
    if named is not None and not _APPLICATION_ID.match(named):
        raise GatewayHTTPError(404, "unknown_application", "no such application")
    if caller.application_id is not None:
        if named is not None and named != caller.application_id:
            raise GatewayHTTPError(
                403, "application_mismatch", "this key belongs to another application"
            )
        named = caller.application_id
    if named is not None:
        app = table.application(caller.tenant, named)
        if app is None:
            raise GatewayHTTPError(404, "unknown_application", "no such application")
        return app
    apps = table.applications(caller.tenant)
    if len(apps) == 1:
        return apps[0]
    if not apps:
        raise GatewayHTTPError(404, "unknown_application", "this organization has no application")
    raise GatewayHTTPError(
        400,
        "application_required",
        "this key does not belong to an application: name one in the X-Umai-Application header",
    )


def _target(request: Request, caller: Caller, model: str) -> tuple[Route, Route | None]:
    """The call target for ``model`` sent by the caller's application, and its fallback."""

    if not _MODEL_REF.match(model):
        raise GatewayHTTPError(400, "invalid_request", "model is not a model name", param="model")
    app = _application(request, caller)
    content = request.app.state.applications.tenant(caller.tenant)
    try:
        route = content.resolve(app, model)
        fallback = content.resolve(app, app.fallback, check_models=False) if app.fallback else None
    except ResolveError as exc:
        raise GatewayHTTPError(exc.status, exc.code, str(exc), param="model") from None
    table = request.app.state.applications
    for target in (route, fallback):
        if target is not None and not table.permits(target.upstream.base_url):
            raise GatewayHTTPError(
                403,
                "provider_not_allowed",
                "this deployment does not allow calls to that provider's address",
                param="model",
            )
    return route, fallback


def call_headers(request_id: str, route: Route) -> dict[str, str]:
    return {"X-Umai-Request-Id": request_id, "X-Umai-Application": route.route_id}


FALLBACK_STATUSES = frozenset({429, 500, 502, 503, 504})


def _deployment_key(settings: Settings, route: Route) -> str | None:
    if route.credential != "secret":
        return None
    secret = settings.gateway_provider_keys.get(route.secret or "")
    if secret is None:
        raise GatewayHTTPError(
            500, "provider_misconfigured", "the provider's key is not configured"
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
        "gateway.fallback request_id=%s application=%s fallback=%s reason=%s",
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
                "this provider needs your provider key in the X-Umai-Provider-Key header",
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
async def list_models(request: Request) -> Response:
    """The application's ``models`` and alias names; without either, its provider's own list."""

    caller = _admit(request)
    app = _application(request, caller)
    names = app.listed_models()
    headers = {"X-Umai-Application": app.application_id}
    if names:
        data = [{"id": n, "object": "model", "created": 0, "owned_by": "umai"} for n in names]
        return JSONResponse({"object": "list", "data": data}, headers=headers)
    content = request.app.state.applications.tenant(caller.tenant)
    provider = content.models_provider(app)
    if provider is None:
        raise GatewayHTTPError(404, "unknown_provider", "this application has no usable provider")
    route = content.resolve(app, f"@{provider.provider_id}/models", check_models=False)
    if not request.app.state.applications.permits(route.upstream.base_url):
        raise GatewayHTTPError(
            403,
            "provider_not_allowed",
            "this deployment does not allow calls to that provider's address",
        )
    response = await request.app.state.upstream.get(
        route, _provider_key(request, route), path="models"
    )
    try:
        data = response.json()
    except ValueError:
        raise GatewayHTTPError(
            502, "upstream_invalid_response", "the upstream answered with invalid JSON"
        ) from None
    if response.status_code >= 400:
        headers["X-Umai-Upstream-Error"] = "true"
    return JSONResponse(
        data, status_code=response.status_code, headers={**forwarded_headers(response), **headers}
    )


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
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
        segments, unscanned = input_segments(body.get("messages"))
    except ContentError as exc:
        raise GatewayHTTPError(400, "invalid_request", str(exc), param="messages") from None
    if unscanned and route.unscanned_parts == "reject":
        raise GatewayHTTPError(
            400,
            "unsupported_content",
            "this application does not accept content parts the guardrail cannot scan "
            "(images, audio, files)",
            param="messages",
        )
    check_chat_request(body)
    limit = state.settings.gateway_max_input_chars
    if text_size(segments) > limit:
        raise GatewayHTTPError(
            413, "input_too_large", f"the text content exceeds {limit} characters"
        )

    call = Call(
        ctx=CallContext(
            request_id, tenant, route, _subject(request, body), record_context(caller, route)
        ),
        provider_key=provider_key,
        session=MaskSession.for_call(s.text for s in segments),
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
    clean_chat_upstream(upstream_body)
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
            masked = call.session.mask_text(
                segment.text, spans, from_input=True, json_text=segment.json_string
            )
            set_text(messages, segment, masked)


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
        clean_chat_answer(data)
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
        blocking = await _check_content(call, message, output_enabled)
        if blocking is None:
            blocking = await settle_tool_calls(call, chat_tool_calls(message))
        if blocking is not None:
            if route.refusal == "error":
                raise refusal_error(call, blocking)
            message["content"] = route.refusal_text
            message.pop("tool_calls", None)
            message.pop("function_call", None)
            choice["finish_reason"] = "content_filter"


def _text_parts(content: Any) -> list[dict[str, Any]]:
    """The text parts of an answer whose ``content`` is an array of parts."""

    return [
        part
        for part in content
        if isinstance(part, dict)
        and part.get("type") in ("text", "output_text")
        and isinstance(part.get("text"), str)
    ]


async def _check_content(call: Call, message: dict[str, Any], output_enabled: bool) -> Check | None:
    """Check ``message.content`` (a string or an array of text parts), mask and restore it in
    place; the blocking check, if any. Content of any other shape is removed: only checked text
    reaches the caller."""

    content = message.get("content")
    if content is None:
        return None
    if isinstance(content, str):
        holders: list[tuple[dict[str, Any], str]] = [(message, "content")]
    elif isinstance(content, list):
        holders = [(part, "text") for part in _text_parts(content)]
    else:
        message["content"] = None
        return None
    texts = [holder[key] for holder, key in holders]
    if output_enabled and any(texts):
        check = await call.guard.check(
            call.ctx, Phase.POST_LLM, [("assistant", text) for text in texts]
        )
        note_output(call, check)
        if not check.allowed:
            return check
        if check.masks:
            texts = [
                call.session.mask(text, spans, from_input=False) if spans else text
                for text, spans in zip(texts, check.spans, strict=True)
            ]
    for (holder, key), text in zip(holders, texts, strict=True):
        holder[key] = call.session.restore(text) if call.route.restore_placeholders else text
    return None


def _log(
    call: Call,
    *,
    status: int,
    usage: Any = None,
    refused: str | None = None,
    upstream_error: bool = False,
    stream: bool = False,
) -> None:
    """The end of a call: one ``gateway.call`` line and one usage record, once."""

    if call.finished:
        return
    call.finished = True
    usage = usage if isinstance(usage, dict) else {}
    _record_call(call, usage)
    log.info(
        "gateway.call request_id=%s tenant=%s application=%s upstream=%s/%s stream=%s status=%s "
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


def _tokens(usage: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = usage.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _record_call(call: Call, usage: dict[str, Any]) -> None:
    """The call's usage records (usage 0.2): never raises, never changes the answer."""

    try:
        route = call.route
        call.guard.record_call(
            call.ctx.record,
            tenant_id=call.ctx.tenant_id,
            occurred_at=datetime.now(UTC),
            bundle_id=route.guardrail,
            provider=route.provider_id or route.upstream.kind,
            model=route.upstream.model,
            input_tokens=_tokens(usage, "prompt_tokens", "input_tokens"),
            output_tokens=_tokens(usage, "completion_tokens", "output_tokens"),
        )
    except Exception:
        log.exception("records.build_failed gateway_call_id=%s", call.ctx.record.gateway_call_id)


def finish_unrecorded(request: Request, status: int) -> None:
    """Close a call its handler did not close (a refusal raised as an error, a timeout, an
    unexpected failure); streams close themselves."""

    call = getattr(request.state, "call", None)
    if not isinstance(call, Call) or call.finished or call.streaming:
        return
    refused = "output" if call.output_action in BLOCKING else None
    _log(call, status=status, refused=refused, upstream_error=status in (502, 504))
