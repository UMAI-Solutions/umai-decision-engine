"""Permission matrix (ADR-0029 §2–§3, §6): the table, the admin rule without rbac, and every
policy-service route declaring its action."""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from umai_decision_engine.contracts.organizations import Role
from umai_decision_engine.control_plane.app import router
from umai_decision_engine.control_plane.authz import MATRIX, Action, allowed

OWN, A, E, P, V, B = (
    Role.OWNER,
    Role.ADMIN,
    Role.POLICY_EDITOR,
    Role.APPROVER,
    Role.VIEWER,
    Role.BILLING,
)

# Expected allow-sets with rbac (Enterprise); the matrix must match exactly.
EXPECTED = {
    Action.GUARDRAIL_READ: {OWN, A, E, P, V},
    Action.DRAFT_WRITE: {OWN, E},
    Action.GUARDRAIL_PUBLISH: {OWN, E},
    Action.GUARDRAIL_ACTIVATE: {OWN, P},
    Action.KEY_CREATE: {OWN, A},
    Action.TENANT_CREATE: set(),
    Action.MEMBER_UPDATE: {OWN, A},
    Action.ORGANIZATION_READ: {OWN, A, E, P, V, B},
    Action.APPLICATION_READ: {OWN, A, E, P, V},
    Action.APPLICATION_WRITE: {OWN, A},
    Action.GATEWAY_READ: {OWN, A, E, P, V},
    Action.GATEWAY_WRITE: {OWN, A},
}


@pytest.mark.parametrize("action", sorted(EXPECTED))
@pytest.mark.parametrize("role", list(Role))
def test_matrix_with_rbac(action: Action, role: Role) -> None:
    assert allowed(action, frozenset({role}), rbac=True) is (role in EXPECTED[action])


def test_every_action_has_a_rule() -> None:
    assert set(MATRIX) == set(Action)


def test_admin_without_rbac_holds_every_non_owner_permission() -> None:
    admin = frozenset({A})
    assert allowed(Action.GUARDRAIL_PUBLISH, admin, rbac=False)
    assert allowed(Action.GUARDRAIL_ACTIVATE, admin, rbac=False)
    assert not allowed(Action.GUARDRAIL_PUBLISH, admin, rbac=True)
    assert not allowed(Action.TENANT_CREATE, admin, rbac=False)  # operator only


def test_billing_and_viewer_cannot_change_policy() -> None:
    for role in (B, V):
        for action in (Action.DRAFT_WRITE, Action.GUARDRAIL_PUBLISH, Action.GUARDRAIL_ACTIVATE):
            assert not allowed(action, frozenset({role}), rbac=True)


def test_no_roles_no_access() -> None:
    assert not any(allowed(action, frozenset(), rbac=False) for action in Action)


def test_changing_policy_needs_a_person() -> None:
    for action in (
        Action.DRAFT_WRITE,
        Action.DRAFT_TEST,
        Action.GUARDRAIL_PUBLISH,
        Action.GUARDRAIL_ACTIVATE,
    ):
        assert not MATRIX[action].operator, action


def _actions(dependant) -> list[Action]:
    found = []
    for dep in dependant.dependencies:
        action = getattr(dep.call, "umai_action", None)
        if action is not None:
            found.append(action)
        found.extend(_actions(dep))
    return found


@pytest.mark.parametrize(
    "route",
    [r for r in router.routes if isinstance(r, APIRoute)],
    ids=lambda r: f"{sorted(r.methods)[0]} {r.path}",
)
def test_every_route_declares_exactly_one_action(route: APIRoute) -> None:
    """Deny by default (ADR-0029 §6): a new route without a declared action fails here."""

    assert len(_actions(route.dependant)) == 1, route.path
