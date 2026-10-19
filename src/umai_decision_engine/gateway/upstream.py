"""Calls to the route's upstream: URL, credential header, explicit timeouts (ADR-0034 §4–§5).

The provider credential is used for this request only and never logged (ADR-0035 §4). Connection
failures and timeouts become gateway errors; provider error responses are passed through.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from umai_decision_engine.contracts.gateway import Route
from umai_decision_engine.gateway.errors import GatewayHTTPError

# Upstream response headers worth passing on to the caller (rate limits, provider request ids).
FORWARDED_HEADERS = (
    "x-request-id",
    "openai-processing-ms",
    "openai-version",
    "retry-after",
    "apim-request-id",
)
FORWARDED_PREFIXES = ("x-ratelimit-",)


def upstream_url(route: Route, path: str = "chat/completions") -> str:
    return f"{route.upstream.base_url.rstrip('/')}/{path}"


def credential_headers(route: Route, key: str | None) -> dict[str, str]:
    if key is None:
        return {}
    if route.upstream.kind == "azure_openai":
        return {"api-key": key}
    return {"Authorization": f"Bearer {key}"}


def forwarded_headers(response: httpx.Response) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in response.headers.items():
        lower = name.lower()
        if lower in FORWARDED_HEADERS or lower.startswith(FORWARDED_PREFIXES):
            out[lower] = value
    return out


class UpstreamClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _request(
        self, route: Route, key: str | None, body: dict[str, Any], *, stream: bool, path: str
    ) -> httpx.Request:
        timeouts = route.timeouts_ms
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            **credential_headers(route, key),
        }
        return self._client.build_request(
            "POST",
            upstream_url(route, path),
            content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            timeout=httpx.Timeout(
                connect=timeouts.connect / 1000,
                read=timeouts.first_byte / 1000,
                write=30.0,
                pool=timeouts.connect / 1000,
            ),
        )

    async def send(
        self,
        route: Route,
        key: str | None,
        body: dict[str, Any],
        *,
        stream: bool = False,
        path: str = "chat/completions",
    ) -> httpx.Response:
        request = self._request(route, key, body, stream=stream, path=path)
        try:
            return await self._client.send(request, stream=stream)
        except httpx.TimeoutException as exc:
            raise GatewayHTTPError(
                504, "upstream_timeout", "the upstream did not answer in time"
            ) from exc
        except httpx.HTTPError as exc:
            raise GatewayHTTPError(
                502, "upstream_unreachable", "the upstream could not be reached"
            ) from exc
