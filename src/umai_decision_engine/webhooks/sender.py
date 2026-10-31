"""One webhook attempt (ADR-0048 §8–§9): POST the body, keep the status code, never follow a
redirect, never read the answer's body, bounded in time from connect to the last header."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from umai_decision_engine.webhooks.guard import BlockedAddress

USER_AGENT = "UMAI-Webhooks/0.1"


@dataclass(frozen=True)
class Attempt:
    outcome: str  # delivered | failed
    status_code: int | None
    error: (
        str | None
    )  # contract webhooks: http_status | redirect | timeout | connection | blocked_address
    duration_ms: float

    @property
    def delivered(self) -> bool:
        return self.outcome == "delivered"


class WebhookSender:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport,
        *,
        connect_timeout_s: float,
        timeout_s: float,
    ) -> None:
        self._timeout_s = timeout_s
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s),
            headers={"user-agent": USER_AGENT},
        )

    async def post(self, url: str, body: bytes, headers: dict[str, str]) -> Attempt:
        started = time.perf_counter()
        status: int | None = None
        error: str | None = None
        try:
            async with asyncio.timeout(self._timeout_s):
                async with self._client.stream(
                    "POST", url, content=body, headers=headers
                ) as answer:
                    status = answer.status_code  # leaving the block closes it unread
        except BlockedAddress:
            error = "blocked_address"
        except (TimeoutError, httpx.TimeoutException):
            error = "timeout"
        except (httpx.HTTPError, OSError):
            error = "connection"
        duration = round((time.perf_counter() - started) * 1000, 1)
        if status is not None:
            if 200 <= status < 300:
                return Attempt("delivered", status, None, duration)
            error = "redirect" if 300 <= status < 400 else "http_status"
        return Attempt("failed", status, error, duration)

    async def aclose(self) -> None:
        await self._client.aclose()


def masked(url: str) -> str:
    """Scheme, host and the last four characters: enough to recognize an endpoint, too little to
    use a credential in its path or query."""

    parts = urlsplit(url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    rest = url[len(f"{parts.scheme}://{parts.netloc}") :]
    tail = rest[-4:] if len(rest) > 5 else ""
    return f"{parts.scheme}://{host}{port}/…{tail}"
