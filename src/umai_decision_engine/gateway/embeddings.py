"""``POST /v1/embeddings``: input checks only (gateway-api 0.5).

Each string in ``input`` is checked (``PRE_LLM``) and masked before it is embedded, so vectors
stored in a retrieval index never encode the masked values. Token arrays cannot be scanned and
follow ``unscanned_parts``. A refusal is always an HTTP 400: there is no text answer to carry a
refusal message. The vectors come back unchanged.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from umai_decision_engine.contracts.common import Phase
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
from umai_decision_engine.gateway.errors import GatewayHTTPError
from umai_decision_engine.gateway.guard import CallContext
from umai_decision_engine.gateway.masking import MaskSession
from umai_decision_engine.gateway.upstream import forwarded_headers

router = APIRouter()


def _texts(value: Any) -> tuple[list[tuple[int | None, str]], int]:
    """(position in a list or None for a single string, text) and the number of token arrays."""

    if isinstance(value, str):
        return [(None, value)], 0
    if isinstance(value, list):
        if value and all(isinstance(v, int) for v in value):
            return [], 1  # one token array
        texts, unscanned = [], 0
        for i, item in enumerate(value):
            if isinstance(item, str):
                texts.append((i, item))
            elif isinstance(item, list):
                unscanned += 1
            else:
                raise GatewayHTTPError(
                    400, "invalid_request", "input items must be strings", param="input"
                )
        return texts, unscanned
    raise GatewayHTTPError(
        400, "invalid_request", "input must be a string or an array", param="input"
    )


@router.post("/v1/embeddings")
async def create_embeddings(request: Request) -> Response:
    started = time.perf_counter()
    request_id = (request.headers.get("x-umai-request-id") or "")[:128] or f"gw-{uuid.uuid4().hex}"
    request.state.request_id = request_id
    state = request.app.state

    caller = _admit(request)
    tenant = caller.tenant
    body = await _read_body(request)
    route, fallback = _target(request, caller, body["model"])
    provider_key = _provider_key(request, route)
    texts, unscanned = _texts(body.get("input"))
    if unscanned and route.unscanned_parts == "reject":
        raise GatewayHTTPError(
            400,
            "unsupported_content",
            "token arrays cannot be scanned on this gateway",
            param="input",
        )
    limit = state.settings.gateway_max_input_chars
    if sum(len(t) for _, t in texts) > limit:
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
    upstream_body = copy.deepcopy(body)
    upstream_body["model"] = route.upstream.model
    if texts:
        check = await call.guard.check(call.ctx, Phase.PRE_LLM, [("user", t) for _, t in texts])
        call.input_check = check
        call.headers["X-Umai-Event-Id"] = check.response.event_id
        call.headers["X-Umai-Input-Decision"] = str(check.decision.action)
        if not check.allowed:
            _log(call, status=400, refused="input")
            raise refusal_error(call, check)
        for (position, text), spans in zip(texts, check.spans, strict=True):
            if not spans:
                continue
            masked = call.session.mask(text, spans, from_input=True)
            if position is None:
                upstream_body["input"] = masked
            else:
                upstream_body["input"][position] = masked
    try:
        async with asyncio.timeout(call.remaining_s()):
            response = await send_upstream(call, upstream_body, path="embeddings")
    except TimeoutError:
        raise GatewayHTTPError(
            504, "upstream_timeout", "the upstream did not answer in time"
        ) from None
    passthrough = forwarded_headers(response)
    if response.status_code >= 400:
        _log(call, status=response.status_code, upstream_error=True)
        return Response(
            response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/json"),
            headers={**passthrough, **call.headers, "X-Umai-Upstream-Error": "true"},
        )
    try:
        data = json.loads(response.content)
    except ValueError:
        raise GatewayHTTPError(
            502, "upstream_invalid_response", "the upstream answer is not valid JSON"
        ) from None
    usage = data.get("usage") if isinstance(data, dict) else None
    _log(call, status=response.status_code, usage=usage)
    return JSONResponse(
        data, status_code=response.status_code, headers={**passthrough, **_decision_headers(call)}
    )
