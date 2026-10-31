"""The audit log's route discipline (ADR-0045): every changing policy-service route declares one
audit action, and every audit action has its route and its target type. A new POST, PUT, PATCH
or DELETE under ``/v1`` without an action fails here, as a route without a permission fails
``test_authz``."""

from __future__ import annotations

from typing import get_args

import pytest
from fastapi.routing import APIRoute

from umai_decision_engine.contracts.audit import AuditAction, TargetType
from umai_decision_engine.control_plane.app import router
from umai_decision_engine.control_plane.audit import (
    CHANGING,
    NOT_AUDITED,
    TARGET,
    AuditRoute,
    audit_action,
)

ROUTES = [r for r in router.routes if isinstance(r, APIRoute)]


def _id(route: APIRoute) -> str:
    return f"{sorted(route.methods)[0]} {route.path}"


@pytest.mark.parametrize(
    "route", [r for r in ROUTES if r.methods & CHANGING and _id(r) not in NOT_AUDITED], ids=_id
)
def test_every_changing_route_declares_an_audit_action(route: APIRoute) -> None:
    assert isinstance(route, AuditRoute)
    assert audit_action(route.dependant) is not None, f"{_id(route)} records no audit event"


def test_the_unaudited_routes_exist_and_are_a_persons_own_sessions() -> None:
    changing = {_id(r) for r in ROUTES if r.methods & CHANGING}
    assert changing >= NOT_AUDITED  # a renamed route must leave this list too
    assert all("/sessions" in route for route in NOT_AUDITED)
    for route in ROUTES:
        if _id(route) in NOT_AUDITED:
            assert audit_action(route.dependant) is None


@pytest.mark.parametrize("route", [r for r in ROUTES if not r.methods & CHANGING], ids=_id)
def test_reads_record_nothing_but_the_export(route: APIRoute) -> None:
    expected = AuditAction.AUDIT_EXPORT if route.path == "/v1/audit-events/export" else None
    assert audit_action(route.dependant) == expected


def test_every_action_has_one_route_and_a_target_type() -> None:
    declared = [audit_action(r.dependant) for r in ROUTES]
    used = [a for a in declared if a is not None]
    assert sorted(used) == sorted(set(used)), "two routes share an audit action"
    assert set(used) == set(AuditAction)
    assert set(TARGET) == set(AuditAction)
    assert set(TARGET.values()) <= set(get_args(TargetType))
