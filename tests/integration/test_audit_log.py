"""The audit log end to end (ADR-0045, contract audit 0.1): changes through the policy service
become events with actor, target and before/after; refused attempts are recorded on every
changing route; the list filters and pages; the CSV export is safe to open in a spreadsheet and
is itself recorded; nothing secret is ever written; retention keeps 400 days. Synthetic persons
only; SQLite always, PostgreSQL with row-level security when configured (CI)."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from tests.integration.test_organizations import (
    ADMIN_URL,
    APP_URL,
    CONTROL,
    KEY,
    _create,
    _reset_postgres,
    person,
)
from umai_decision_engine.contracts.audit import CSV_COLUMNS, AuditAction, AuditEventPage
from umai_decision_engine.control_plane.actors import public_key_b64
from umai_decision_engine.control_plane.app import create_app, router
from umai_decision_engine.control_plane.audit import AuditLog, audit_action
from umai_decision_engine.control_plane.authz import MATRIX, OPERATOR
from umai_decision_engine.control_plane.records import apply_retention
from umai_decision_engine.db.migrate import upgrade
from umai_decision_engine.db.repositories import TenantRepository
from umai_decision_engine.db.session import make_engine, sqlite_url
from umai_decision_engine.settings import Settings

ORG = "acme-security"


@pytest.fixture(params=["sqlite", "postgres"])
def api(request, write_bundle, detector, tmp_path):
    database = {}
    if request.param == "postgres":
        if not (ADMIN_URL and APP_URL):
            pytest.skip("no test Postgres configured")
        _reset_postgres()
        database = {"database_url": APP_URL, "database_admin_url": ADMIN_URL}
    write_bundle(bundle_yaml(), active=1)  # the starter template
    settings = Settings(
        **database,
        env="test",
        require_signed_bundles=False,
        policy_dir=write_bundle.root,
        control_db_path=tmp_path / "control.sqlite",
        control_api_token=CONTROL,
        actor_assertion="required",
        actor_public_keys={"console-1": public_key_b64(KEY)},
        starter_guardrail="gr-test",
        key_registry_dir=tmp_path / "keys",
        applications_dir=tmp_path / "applications",
    )
    with TestClient(create_app(settings, detector=detector)) as client:
        client.headers["Authorization"] = f"Bearer {CONTROL}"
        yield client


def _owner() -> dict[str, str]:
    return person("ada", tenant=ORG)


def _events(api: TestClient, headers: dict[str, str] | None = None, **params) -> list[dict]:
    response = api.get("/v1/audit-events", params=params, headers=headers or _owner())
    assert response.status_code == 200, response.text
    page = AuditEventPage.model_validate(response.json())  # the contract shape
    return [e.model_dump(mode="json", exclude_none=True) for e in page.events]


def _join(api: TestClient, who: str, roles: list[str]) -> str:
    """Invite and accept; returns the new member's user id."""

    invited = api.post(
        f"/v1/organizations/{ORG}/invitations",
        json={"email": f"{who}@example.com", "roles": roles},
        headers=_owner(),
    )
    assert invited.status_code == 201, invited.text
    accepted = api.post(
        "/v1/invitations/accept",
        json={"organization": ORG, "token": invited.json()["token"]},
        headers=person(who),
    )
    assert accepted.status_code == 200, accepted.text
    members = api.get(f"/v1/organizations/{ORG}/members", headers=_owner()).json()["members"]
    return next(m["user"]["id"] for m in members if m["user"]["email"] == f"{who}@example.com")


# ------------------------------------------------------------------ changes


def test_a_day_of_changes_is_in_the_audit_log_without_secrets(api) -> None:
    _create(api)
    invited = api.post(
        f"/v1/organizations/{ORG}/invitations",
        json={"email": "grace@example.com", "roles": ["viewer"]},
        headers=_owner(),
    ).json()
    api.post(
        "/v1/invitations/accept",
        json={"organization": ORG, "token": invited["token"]},
        headers=person("grace"),
    )
    members = api.get(f"/v1/organizations/{ORG}/members", headers=_owner()).json()["members"]
    grace = next(m["user"]["id"] for m in members if m["user"]["email"] == "grace@example.com")
    ada = next(m["user"]["id"] for m in members if m["user"]["email"] == "ada@example.com")
    promoted = api.patch(
        f"/v1/organizations/{ORG}/members/{grace}", json={"roles": ["admin"]}, headers=_owner()
    )
    assert promoted.status_code == 200, promoted.text
    key = api.post(f"/v1/tenants/{ORG}/keys", json={"name": "ci"}, headers=_owner()).json()
    key_id = key["key"]["key_id"]
    assert api.delete(f"/v1/tenants/{ORG}/keys/{key_id}", headers=_owner()).status_code == 200
    assert api.post("/v1/guardrails/gr-test/draft", json={}, headers=_owner()).status_code == 201
    published = api.post(
        "/v1/guardrails/gr-test/draft/publish",
        json={"activate": True, "note": "weekly review"},
        headers=_owner(),
    )
    assert published.status_code == 200, published.text
    rolled_back = api.post("/v1/guardrails/gr-test/activate", json={"version": 1}, headers=_owner())
    assert rolled_back.status_code == 200, rolled_back.text
    saved = api.put(
        "/v1/applications/hr-assistant",
        json={"guardrail": "gr-test", "name": "HR assistant"},
        headers=_owner(),
    )
    assert saved.status_code == 201, saved.text
    assert (
        api.put("/v1/gateway/settings", json={"stream_mode": "buffer"}, headers=_owner())
    ).status_code == 200
    renamed = api.patch(f"/v1/organizations/{ORG}", json={"name": "Acme Sec"}, headers=_owner())
    assert renamed.status_code == 200, renamed.text

    events = _events(api)
    assert [e["action"] for e in reversed(events)] == [
        "organization.create",
        "invitation.create",
        "invitation.accept",
        "member.update",
        "key.create",
        "key.revoke",
        "guardrail.draft.create",
        "guardrail.publish",
        "guardrail.activate",
        "application.save",
        "gateway.settings.update",
        "organization.update",
    ]
    by = {e["action"]: e for e in events}
    assert all(e["outcome"] == "success" and e["tenant_id"] == ORG for e in events)
    assert all(e["event_id"].startswith("au-") and e["request_id"] for e in events)
    created = by["organization.create"]
    assert created["actor"] == {
        "type": "person",
        "sub": "auth0|ada",
        "user_id": ada,
        "email": "ada@example.com",  # resolved when read, never stored
        "name": "Ada",
    }
    assert created["target"] == {"type": "organization", "id": ORG}
    assert created["details"]["starter_guardrail"] == {"id": "gr-test", "version": 1}
    assert by["invitation.accept"]["actor"]["sub"] == "auth0|grace"
    assert by["invitation.accept"]["target"] == {"type": "member", "id": grace}
    assert by["member.update"]["target"] == {"type": "member", "id": grace}
    assert by["member.update"]["details"] == {"roles": {"before": ["viewer"], "after": ["admin"]}}
    assert by["key.create"]["target"] == {"type": "api_key", "id": key_id}
    assert by["key.create"]["details"]["name"] == "ci"
    assert by["guardrail.publish"]["details"] == {
        "version": 2,
        "activated": True,
        "previous_active": 1,
        "note": "weekly review",
    }
    assert by["guardrail.activate"]["details"]["version"] == {"before": 2, "after": 1}
    assert by["application.save"]["details"] == {"created": True, "guardrail": "gr-test"}
    assert by["gateway.settings.update"]["target"] == {"type": "gateway_settings"}
    assert by["gateway.settings.update"]["details"] == {"changed": ["stream_mode"]}
    assert by["organization.update"]["details"]["name"] == {
        "before": "Acme Security",
        "after": "Acme Sec",
    }
    stored = json.dumps([e["details"] for e in events])
    assert key["secret"] not in stored and invited["token"] not in stored
    assert "grace@example.com" not in stored  # the invitee's address stays in the invitation


def test_operator_changes_name_the_operator_and_land_in_the_target_tenant(api) -> None:
    created = api.post("/v1/tenants", json={"id": "globex", "name": "Globex"})
    assert created.status_code == 201, created.text
    plan = api.put("/v1/tenants/globex/plan", json={"edition": "pro"})
    assert plan.status_code == 200, plan.text
    events = _events(api, headers={"X-Umai-Tenant": "globex"})
    assert [e["action"] for e in reversed(events)] == ["tenant.create", "tenant.plan"]
    assert events[0]["actor"] == {"type": "operator"}
    assert events[0]["details"]["edition"] == {"before": "community", "after": "pro"}
    assert _events(api, headers={"X-Umai-Tenant": "default"}) == []


# ------------------------------------------------------------------ refused attempts


def _permission(route: APIRoute):
    def walk(dependant):
        for dep in dependant.dependencies:
            found = getattr(dep.call, "umai_action", None) or walk(dep)
            if found is not None:
                return found
        return None

    return walk(route.dependant)


_PATH_VALUES = {
    "bundle_id": "gr-test",
    "tenant_id": ORG,
    "org_id": ORG,
    "key_id": "k0000000",
    "user_id": str(uuid.UUID(int=7)),
    "invitation_id": str(uuid.UUID(int=8)),
    "application_id": "hr-assistant",
    "provider_id": "contoso",
    "checkout_id": "co-1",
    "endpoint_id": "we-0000000000000000",
}
AUDITED = [
    r for r in router.routes if isinstance(r, APIRoute) and audit_action(r.dependant) is not None
]


def test_every_audited_route_records_a_refused_attempt(api) -> None:
    """A walk over the router: each changing route (and the export), called by someone without
    the right, answers 403 and leaves a ``denied`` event with its action."""

    _create(api)
    _join(api, "vic", ["viewer"])
    expected = set()
    for route in AUDITED:
        method = sorted(route.methods)[0]
        path = route.path_format.format(**_PATH_VALUES)
        if MATRIX[_permission(route)].self_service:  # a person's own act: refused to the token
            headers = {"X-Umai-Tenant": ORG}
        else:
            headers = person("vic", tenant=ORG)
        response = api.request(method, path, json={} if method != "GET" else None, headers=headers)
        assert response.status_code == 403, (method, path, response.text)
        expected.add(str(audit_action(route.dependant)))
    denied = _events(api, outcome="denied", limit=200)
    assert {e["action"] for e in denied} == expected
    for event in denied:
        assert event["details"]["reason"] in {"forbidden", "actor_required"}
    revoke = next(e for e in denied if e["action"] == "key.revoke")
    assert revoke["actor"]["sub"] == "auth0|vic"
    assert revoke["target"] == {"type": "api_key", "id": "k0000000"}


def test_a_refused_attempt_on_another_tenant_stays_in_the_actors_own(api) -> None:
    _create(api)
    _create(api, "bob", org_id="globex-labs", name="Globex Labs")
    refused = api.post(
        f"/v1/tenants/{ORG}/keys", json={"name": "x"}, headers=person("bob", tenant="globex-labs")
    )
    assert refused.status_code == 403
    assert _events(api, outcome="denied") == []  # never written into the other tenant's log
    bobs = _events(api, headers=person("bob", tenant="globex-labs"), outcome="denied")
    assert [(e["action"], e["details"]["reason"]) for e in bobs] == [
        ("key.create", "tenant_mismatch")
    ]


# ------------------------------------------------------------------ reading and export


def test_filters_pages_and_permissions(api) -> None:
    _create(api)
    grace = _join(api, "grace", ["admin"])
    for name in ("one", "two", "three"):
        api.post(f"/v1/tenants/{ORG}/keys", json={"name": name}, headers=_owner())
    api.post(f"/v1/tenants/{ORG}/keys", json={"name": "four"}, headers=person("grace", tenant=ORG))

    keys = _events(api, action="key")
    assert [e["details"]["name"] for e in keys] == ["four", "three", "two", "one"]
    assert [e["details"]["name"] for e in _events(api, actor=grace, action="key")] == ["four"]
    assert len(_events(api, actor="auth0|grace")) == 2  # accept and the key, by subject too
    assert _events(api, action="key.revoke") == []
    assert {e["action"] for e in _events(api, action="invitation")} == {
        "invitation.create",
        "invitation.accept",
    }
    first = api.get("/v1/audit-events", params={"limit": 2, "action": "key"}, headers=_owner())
    page = first.json()
    assert len(page["events"]) == 2 and page["next_cursor"]
    rest = _events(api, action="key", cursor=page["next_cursor"])
    assert [e["details"]["name"] for e in rest] == ["two", "one"]
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert _events(api, since=future) == []
    assert (
        api.get("/v1/audit-events", params={"cursor": "nope"}, headers=_owner()).status_code == 400
    )
    assert (
        api.get("/v1/audit-events", params={"action": "key.%"}, headers=_owner()).status_code == 422
    )
    _join(api, "vic", ["viewer"])
    assert api.get("/v1/audit-events", headers=person("vic", tenant=ORG)).status_code == 403
    assert api.get("/v1/audit-events", headers=person("grace", tenant=ORG)).status_code == 200


def test_csv_export_is_spreadsheet_safe_and_recorded(api) -> None:
    _create(api)
    api.post(
        f"/v1/tenants/{ORG}/keys",
        json={"name": '=HYPERLINK("https://x.example")'},
        headers=_owner(),
    )
    response = api.get("/v1/audit-events/export", params={"action": "key"}, headers=_owner())
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert f'filename="audit-{ORG}-' in response.headers["content-disposition"]
    assert response.headers["x-umai-export-rows"] == "1"
    assert response.headers["x-umai-export-truncated"] == "false"
    rows = list(csv.reader(io.StringIO(response.text)))
    assert tuple(rows[0]) == CSV_COLUMNS
    (row,) = [dict(zip(CSV_COLUMNS, r, strict=True)) for r in rows[1:]]
    assert row["action"] == "key.create" and row["actor_email"] == "ada@example.com"
    details = json.loads(row["details"])
    assert details["name"].startswith("=HYPERLINK")  # the JSON cell starts with "{": inert
    exported = _events(api, action="audit")
    assert [(e["action"], e["details"]["rows"], e["details"]["action"]) for e in exported] == [
        ("audit.export", 1, "key")
    ]


def test_formula_cells_are_quoted() -> None:
    from umai_decision_engine.control_plane.audit import _cell

    assert _cell("=1+1") == "'=1+1" and _cell("@SUM(A1)") == "'@SUM(A1)"
    assert _cell("-2") == "'-2" and _cell("key.create") == "key.create" and _cell(None) == ""


def test_a_failed_audit_write_is_logged_and_the_change_stands(api, tmp_path, caplog) -> None:
    _create(api)
    broken = make_engine(sqlite_url(tmp_path / "no-tables.sqlite"))  # no audit_events table
    api.app.state.audit = AuditLog(broken)
    with caplog.at_level(logging.ERROR, logger="umai.control_plane.audit"):
        created = api.post(f"/v1/tenants/{ORG}/keys", json={"name": "ci"}, headers=_owner())
    assert created.status_code == 201
    assert "audit.write_failed" in caplog.text and "action=key.create" in caplog.text
    assert created.json()["secret"] not in caplog.text
    api.portal.call(broken.dispose)


# ------------------------------------------------------------------ retention


def test_retention_keeps_audit_events_400_days(tmp_path) -> None:
    async def go() -> tuple[dict, list[str]]:
        url = sqlite_url(tmp_path / "audit.sqlite")
        upgrade(url)
        engine = make_engine(url)
        tenant = await TenantRepository(engine).get_or_create("default", "eu")
        audit = AuditLog(engine)
        now = datetime(2026, 12, 1, tzinfo=UTC)
        for days in (401, 399):
            await audit.write(
                tenant,
                actor=OPERATOR,
                action=AuditAction.TENANT_PLAN,
                target_type="tenant",
                target_id="default",
                outcome="success",
                request_id=f"req-{days}",
                details={},
                occurred_at=now - timedelta(days=days),
            )
        deleted = await apply_retention(
            engine, decision_days=30, usage_days=400, audit_days=400, now=now
        )
        page = await audit.page(tenant)
        await engine.dispose()
        return deleted, [e.request_id for e in page.events]

    deleted, kept = asyncio.run(go())
    assert deleted["audit_events"] == 1
    assert kept == ["req-399"]
