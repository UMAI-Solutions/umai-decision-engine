"""Admission on every ``/v1`` request (ADR-0023): who is calling, for which tenant, with which
scopes, within which rate. Replaces the single shared-token check of ADR-0016 while keeping
that token valid for self-hosted installs (§8).

Errors follow the evaluation API contract 0.6: 401 ``invalid_credential``, 403
``tenant_mismatch`` / ``insufficient_scope``, 429 ``rate_limited`` with ``Retry-After``.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, Request

from umai_decision_engine.contracts.credentials import Scope
from umai_decision_engine.tenancy.keys import hashes_match, parse_key
from umai_decision_engine.tenancy.limits import RateLimiter, retry_after_seconds
from umai_decision_engine.tenancy.registry import KeyRegistry

log = logging.getLogger("umai.tenancy.admission")

ALL_SCOPES: frozenset[Scope] = frozenset(Scope)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. ``tenant_id`` is ``None`` only for the shared operator token,
    which acts as the tenant named in the request body (ADR-0023 §8)."""

    kind: str  # "api_key" | "shared_token"
    tenant_id: str | None
    scopes: frozenset[Scope]
    key_id: str | None = None
    env: str | None = None
    gateway_id: str | None = None  # the key's gateway (credentials 0.5, ADR-0038)

    @property
    def rate_limit_key(self) -> str:
        return f"key:{self.key_id}" if self.key_id else "shared"

    def bind_tenant(self, body_tenant_id: str | None) -> str:
        """The tenant a request runs as: the credential's tenant is authoritative (§5)."""

        if self.tenant_id is None:
            if not body_tenant_id:
                raise HTTPException(
                    400, {"error": "tenant_required", "detail": "tenant_id is required"}
                )
            return body_tenant_id
        if body_tenant_id is not None and body_tenant_id != self.tenant_id:
            raise HTTPException(
                403,
                {
                    "error": "tenant_mismatch",
                    "detail": "the credential belongs to another tenant",
                },
            )
        return self.tenant_id


def _unauthorized() -> HTTPException:
    return HTTPException(
        401,
        {"error": "invalid_credential", "detail": "missing or invalid API credential"},
        headers={"WWW-Authenticate": "Bearer"},
    )


class Admission:
    def __init__(
        self,
        *,
        shared_token: str | None,
        registry: KeyRegistry | None,
        limiter: RateLimiter | None,
        development: bool,
    ) -> None:
        self.shared_token = shared_token
        self.registry = registry
        self.limiter = limiter
        self.development = development

    def authenticate(self, authorization: str | None, *, now: datetime | None = None) -> Principal:
        scheme, _, value = (authorization or "").partition(" ")
        value = value.strip()
        if scheme.lower() != "bearer" or not value:
            if self.development and self.shared_token is None and self.registry is None:
                return Principal("shared_token", None, ALL_SCOPES)  # open development engine
            raise _unauthorized()
        parsed = parse_key(value)
        if parsed is not None:
            if self.registry is None:
                raise _unauthorized()
            key = self.registry.lookup(parsed.key_id)
            if key is None or not hashes_match(parsed.secret_sha256, key.entry.secret_sha256):
                raise _unauthorized()
            if not key.usable_at(now or datetime.now(UTC)):
                raise _unauthorized()
            return Principal(
                "api_key",
                key.tenant_id,
                frozenset(key.entry.scopes),
                key_id=key.key_id,
                env=key.entry.env,
                gateway_id=key.entry.gateway_id,
            )
        if self.shared_token is not None and hmac.compare_digest(
            value.encode(), self.shared_token.encode()
        ):
            return Principal("shared_token", None, ALL_SCOPES)
        raise _unauthorized()

    def admit(self, principal: Principal) -> None:
        if self.limiter is None:
            return
        wait = self.limiter.acquire(principal.rate_limit_key)
        if wait is not None:
            raise HTTPException(
                429,
                {"error": "rate_limited", "detail": "too many requests for this credential"},
                headers={"Retry-After": str(retry_after_seconds(wait))},
            )


async def authenticate(request: Request) -> Principal:
    """FastAPI dependency: the principal for this request (rate-limited)."""

    admission: Admission = request.app.state.admission
    principal = admission.authenticate(request.headers.get("authorization"))
    admission.admit(principal)
    request.state.principal = principal
    return principal


def require_scope(scope: Scope):
    async def _check(request: Request) -> Principal:
        principal = await authenticate(request)
        if scope not in principal.scopes:
            raise HTTPException(
                403,
                {"error": "insufficient_scope", "detail": f"scope {scope.value} is required"},
            )
        return principal

    return _check
