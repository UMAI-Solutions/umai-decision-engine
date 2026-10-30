"""Admission on every ``/v1`` request (ADR-0023): who is calling, for which tenant, with which
scopes, within which rate. Replaces the single shared-token check of ADR-0016 while keeping
that token valid for self-hosted installs (§8).

Errors follow the evaluation API contract 0.6: 401 ``invalid_credential``, 403
``tenant_mismatch`` / ``insufficient_scope``, 429 ``rate_limited`` with ``Retry-After``; since
0.13 429 ``quota_exceeded`` for a tenant's used monthly requests (ADR-0043).
"""

from __future__ import annotations

import hmac
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, Request

from umai_decision_engine.contracts.credentials import Scope
from umai_decision_engine.contracts.entitlements import TenantEntitlements
from umai_decision_engine.contracts.evaluation import QuotaExceededDetail
from umai_decision_engine.tenancy.keys import hashes_match, parse_key
from umai_decision_engine.tenancy.limits import RateLimiter, retry_after_seconds
from umai_decision_engine.tenancy.quota import QuotaCounter, QuotaRefusal, quota_environment
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
    application_id: str | None = None  # the key's application (credentials 0.6, ADR-0039)

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


def quota_exceeded(refusal: QuotaRefusal, now: datetime | None = None) -> HTTPException:
    """429 ``quota_exceeded`` (evaluation API 0.13): the limit, the count, when it resets."""

    wait = (refusal.resets_at - (now or datetime.now(UTC))).total_seconds()
    detail = QuotaExceededDetail(
        detail=(
            f"this organization used its {refusal.limit} {refusal.environment} requests for "
            f"the month; the quota resets at {refusal.resets_at.isoformat()}"
        ),
        limit=refusal.limit,
        used=refusal.used,
        environment=refusal.environment,
        resets_at=refusal.resets_at,
    )
    return HTTPException(
        429,
        detail.model_dump(mode="json"),
        headers={"Retry-After": str(max(1, math.ceil(wait)))},
    )


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
        quota: QuotaCounter | None = None,
    ) -> None:
        self.shared_token = shared_token
        self.registry = registry
        self.limiter = limiter
        self.development = development
        self.quota = quota

    def limits(self, tenant_id: str) -> TenantEntitlements | None:
        """The tenant's limits from its signed snapshot (ADR-0043 D1); ``None``: none."""

        return self.registry.entitlements(tenant_id) if self.registry is not None else None

    def max_input_chars(self, tenant_id: str, deployment: int) -> int:
        """The tenant's prompt-size limit, never above the deployment's."""

        limits = self.limits(tenant_id)
        if limits is None or limits.max_input_chars is None:
            return deployment
        return min(deployment, limits.max_input_chars)

    def admit_quota(self, tenant_id: str, key_env: str | None) -> None:
        """Count one request toward the tenant's month, or refuse it with 429 before anything
        is evaluated (ADR-0043 D2, D5). Call it after every other admission check, so a refused
        request is never counted."""

        if self.quota is None:
            return
        limits = self.limits(tenant_id)
        if limits is None:
            return
        environment = quota_environment(key_env)
        refusal = self.quota.admit(tenant_id, environment, limits.monthly(environment))
        if refusal is None:
            return
        log.info(
            "admission.refused reason=quota_exceeded tenant=%s environment=%s used=%d limit=%d",
            tenant_id,
            environment,
            refusal.used,
            refusal.limit,
        )
        raise quota_exceeded(refusal)

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
                application_id=key.entry.application_id,
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
