"""Gateway errors in the OpenAI error shape (contract gateway-api, Gateway errors).

Messages name codes, ids and entity types only; they never echo request or upstream content
(ADR-0035 §3).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from umai_decision_engine.contracts.gateway import (
    GatewayError,
    GatewayErrorBody,
    UmaiDecisionInfo,
)

log = logging.getLogger("umai.gateway")


class GatewayHTTPError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        type_: str = "umai_gateway",
        param: str | None = None,
        headers: dict[str, str] | None = None,
        umai: UmaiDecisionInfo | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.type = type_
        self.param = param
        self.headers = headers or {}
        self.umai = umai

    def body(self) -> dict[str, Any]:
        error = GatewayError(
            message=self.message,
            type=self.type,  # type: ignore[arg-type]
            param=self.param,
            code=self.code,
            umai=self.umai,
        )
        return GatewayErrorBody(error=error).model_dump(mode="json", exclude_none=True)

    def response(self) -> JSONResponse:
        return JSONResponse(self.body(), status_code=self.status, headers=self.headers)


def from_admission(exc: HTTPException) -> GatewayHTTPError:
    """Admission raises the engine's ``{"error", "detail"}`` shape; the gateway speaks OpenAI's."""

    detail = exc.detail if isinstance(exc.detail, dict) else {}
    return GatewayHTTPError(
        exc.status_code,
        str(detail.get("error", "request_refused")),
        str(detail.get("detail", "the request was refused")),
        headers=dict(exc.headers or {}),
    )


async def gateway_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, GatewayHTTPError)
    response = exc.response()
    request_id = getattr(request.state, "request_id", None)
    if exc.type != "umai_guardrail":  # guardrail refusals are in gateway.call already
        # Refused before or around the guardrail (selection, provider, model, upstream): ids
        # and codes only, so an administrator can see why an application's calls fail.
        log.info(
            "gateway.refused request_id=%s path=%s status=%s code=%s",
            request_id,
            request.url.path,
            exc.status,
            exc.code,
        )
    if request_id:
        response.headers["X-Umai-Request-Id"] = request_id
    return response
