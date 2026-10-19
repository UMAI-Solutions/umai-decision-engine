"""Authorization in the policy service (ADR-0029): who may do what, decided here, in code.

One table maps every action to the roles that may perform it; every route declares its action
through :func:`authorize`, and a test fails when a route has none (deny by default, §6). The
console hides what a person may not do; this module refuses it.

Principals: a **person**, named by a verified on-behalf-of assertion (``X-Umai-Actor``, ADR-0023
§1) with the roles of one tenant; or the **operator**, the control token without an assertion.
In ``required`` mode the operator may run operator actions only (tenants, API keys); in
``optional`` mode (development, single-operator installs) it may run every action."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from fastapi import HTTPException, Request

from umai_decision_engine.contracts.organizations import Role
from umai_decision_engine.control_plane.actors import (
    ACTOR_HEADER,
    ActorError,
    verify_actor_assertion,
)

log = logging.getLogger("umai.control_plane.authz")


class Action(StrEnum):
    CATALOG_READ = "catalog.read"
    GUARDRAIL_READ = "guardrail.read"
    DRAFT_WRITE = "guardrail.draft.write"
    DRAFT_TEST = "guardrail.draft.test"
    GUARDRAIL_PUBLISH = "guardrail.publish"
    GUARDRAIL_ACTIVATE = "guardrail.activate"
    TENANT_READ = "tenant.read"
    TENANT_CREATE = "tenant.create"
    KEY_READ = "key.read"
    KEY_CREATE = "key.create"
    KEY_REVOKE = "key.revoke"
    KEY_PUBLISH = "key.publish"
    # organizations contract 0.1 (routes arrive with slice (b) of the sign-in rework)
    ORGANIZATION_READ = "organization.read"
    ORGANIZATION_UPDATE = "organization.update"
    MEMBER_READ = "member.read"
    MEMBER_UPDATE = "member.update"
    MEMBER_REMOVE = "member.remove"
    INVITATION_MANAGE = "invitation.manage"
    DOMAIN_MANAGE = "domain.manage"


@dataclass(frozen=True)
class Rule:
    roles: frozenset[Role]
    operator: bool  # may the control token alone run it when assertions are required?


def _r(*roles: Role, operator: bool = False) -> Rule:
    return Rule(frozenset(roles), operator)


_O, _A, _E, _P, _V, _B = (
    Role.OWNER,
    Role.ADMIN,
    Role.POLICY_EDITOR,
    Role.APPROVER,
    Role.VIEWER,
    Role.BILLING,
)

# The permission matrix (ADR-0029 §2–§3). Publishing and activating always need a person
# (ADR-0023 §1: a service credential alone cannot name an actor).
MATRIX: dict[Action, Rule] = {
    Action.CATALOG_READ: _r(_O, _A, _E, _P, _V, _B, operator=True),
    Action.GUARDRAIL_READ: _r(_O, _A, _E, _P, _V, operator=True),
    Action.DRAFT_WRITE: _r(_O, _E, operator=True),
    Action.DRAFT_TEST: _r(_O, _E, operator=True),
    Action.GUARDRAIL_PUBLISH: _r(_O, _E),
    Action.GUARDRAIL_ACTIVATE: _r(_O, _P),
    Action.TENANT_READ: _r(operator=True),
    Action.TENANT_CREATE: _r(operator=True),
    Action.KEY_READ: _r(_O, _A, operator=True),
    Action.KEY_CREATE: _r(_O, _A, operator=True),
    Action.KEY_REVOKE: _r(_O, _A, operator=True),
    Action.KEY_PUBLISH: _r(operator=True),
    Action.ORGANIZATION_READ: _r(_O, _A, _E, _P, _V, _B),
    Action.ORGANIZATION_UPDATE: _r(_O, _A),
    Action.MEMBER_READ: _r(_O, _A),
    Action.MEMBER_UPDATE: _r(_O, _A),
    Action.MEMBER_REMOVE: _r(_O, _A),
    Action.INVITATION_MANAGE: _r(_O, _A),
    Action.DOMAIN_MANAGE: _r(_O, _A),
}

_ADMIN_WITHOUT_RBAC = frozenset({_A, _E, _P, _V, _B})


def effective_roles(roles: frozenset[Role], *, rbac: bool) -> frozenset[Role]:
    """Without the ``rbac`` entitlement a tenant has owners and admins only, and an admin holds
    every permission except the owner-only ones (ADR-0029 §2)."""

    if not rbac and _A in roles:
        return roles | _ADMIN_WITHOUT_RBAC
    return roles


def allowed(action: Action, roles: frozenset[Role], *, rbac: bool) -> bool:
    rule = MATRIX.get(action)
    if rule is None:  # deny by default (§6)
        return False
    return bool(rule.roles & effective_roles(roles, rbac=rbac))


@dataclass(frozen=True)
class Actor:
    kind: Literal["person", "operator"]
    sub: str | None = None
    session_id: str | None = None
    tenant_id: str | None = None
    roles: frozenset[Role] = frozenset()

    def record(self) -> dict[str, str]:
        """How the change log and key records name the actor (no e-mail, no content)."""

        if self.kind == "operator":
            return {"type": "operator"}
        return {"type": "person", "sub": self.sub or ""}

    @property
    def label(self) -> str:
        return "operator" if self.kind == "operator" else f"person:{self.sub}"


OPERATOR = Actor(kind="operator")


def _error(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status, {"error": code, "detail": detail})


def actor_of(request: Request) -> Actor:
    """The principal of a request whose control token is already verified (``require_token``)."""

    cached = getattr(request.state, "actor", None)
    if cached is not None:
        return cached
    token = request.headers.get(ACTOR_HEADER)
    if token is None:
        actor = OPERATOR
    else:
        settings = request.app.state.settings
        try:
            claims = verify_actor_assertion(
                token,
                request.app.state.actor_keys,
                issuer=settings.actor_issuer,
                audience=settings.actor_audience,
            )
        except ActorError as exc:
            log.warning("authz.invalid_actor reason=%s", exc)
            raise _error(401, "invalid_actor", str(exc)) from exc
        actor = Actor(
            kind="person",
            sub=claims.sub,
            session_id=claims.session_id,
            tenant_id=claims.tenant_id,
            roles=frozenset(claims.roles),
        )
    request.state.actor = actor
    return actor


def authorize(action: Action) -> Callable[[Request], Coroutine[Any, Any, Actor]]:
    """Route dependency: the actor, if it may perform ``action``; HTTP 401/403 otherwise.
    Tenant roles apply to the assertion's tenant only; routes check the target tenant against it
    (:func:`check_tenant`)."""

    rule = MATRIX[action]  # a KeyError here means a route names an action the table lacks

    async def dependency(request: Request) -> Actor:
        actor = actor_of(request)
        mode = request.app.state.settings.actor_assertion
        if actor.kind == "operator":
            if mode == "optional" or rule.operator:
                return actor
            log.warning("authz.denied action=%s actor=operator reason=actor_required", action)
            raise _error(403, "actor_required", f"{action} needs an on-behalf-of assertion")
        # Until entitlements reach the control plane (ADR-0019), every tenant is treated as
        # without rbac: admins hold every non-owner permission (ADR-0029 §2).
        if not allowed(action, actor.roles, rbac=False):
            log.warning(
                "authz.denied action=%s actor=%s tenant=%s", action, actor.label, actor.tenant_id
            )
            raise _error(403, "forbidden", f"{action} is not allowed for your roles")
        return actor

    dependency.umai_action = action  # type: ignore[attr-defined]  # read by the route test
    return dependency


def check_tenant(actor: Actor, tenant_id: str) -> None:
    """A person acts only in the tenant their assertion names (ADR-0033 §1)."""

    if actor.kind == "person" and actor.tenant_id != tenant_id:
        log.warning(
            "authz.denied actor=%s tenant=%s target=%s reason=tenant_mismatch",
            actor.label,
            actor.tenant_id,
            tenant_id,
        )
        raise _error(403, "tenant_mismatch", "the assertion names another tenant")
