"""Browser connections end to end (ADR-0058, contracts browser-extension 0.1, credentials 0.10,
tagged-action 0.8): an admin chooses the browser application, a member connects their own
browser, the engine records the browser's calls with the person and the site, and the
connection goes when the person leaves. The policy service and the engine share one SQLite
store, one key-registry directory and one applications directory, as on the single VM.
Persons are synthetic; assertions are signed as the console signs them."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.integration.test_organizations import KEY, person
from umai_decision_engine.api.app import create_app as create_engine_app
from umai_decision_engine.contracts.browser import MAX_CONNECTIONS_PER_PERSON
from umai_decision_engine.control_plane.actors import public_key_b64
from umai_decision_engine.control_plane.app import create_app
from umai_decision_engine.db.models import AuditEventRow
from umai_decision_engine.db.session import make_engine, sqlite_url, tenant_session
from umai_decision_engine.settings import Settings

CONTROL = "control-token"
ORG = "acme-security"
TCKN = "10000000146"  # synthetic test vector (docs/reference/turkish-identifiers.md)


@pytest.fixture
def stack(write_bundle, detector, tmp_path):
    from tests.conftest import bundle_yaml

    write_bundle(bundle_yaml(), active=1)  # the starter template: gr-test of the default tenant
    common = {
        "env": "test",
        "require_signed_bundles": False,
        "policy_dir": write_bundle.root,
        "key_registry_dir": tmp_path / "keys",
        "applications_dir": tmp_path / "applications",
    }
    control = Settings(
        **common,
        control_db_path=tmp_path / "control.sqlite",
        control_api_token=CONTROL,
        actor_assertion="required",
        actor_public_keys={"console-1": public_key_b64(KEY)},
        starter_guardrail="gr-test",
    )
    engine = Settings(
        **common,
        key_registry_poll_interval_s=0,
        bundle_poll_interval_s=0,
        applications_poll_interval_s=0,
        l0_threads=1,
        records_sink="database",
        database_url=sqlite_url(tmp_path / "control.sqlite"),
        records_flush_interval_ms=10,
    )
    with TestClient(create_app(control, detector=detector)) as client:
        client.headers["Authorization"] = f"Bearer {CONTROL}"
        client.engine_settings = engine
        client.detector = detector
        client.db_url = sqlite_url(tmp_path / "control.sqlite")
        yield client


def _error(response) -> str:
    return response.json()["detail"]["error"]


def _owner() -> dict[str, str]:
    return person("ada", tenant=ORG)


def _organization(api, *members: tuple[str, str]) -> None:
    """Ada owns the organization; each (name, role) member is invited and accepts."""

    body = {
        "name": "Acme Security",
        "id": ORG,
        "region": "sh",
        "accept_terms": True,
        "terms_version": "1",
    }
    assert api.post("/v1/organizations", json=body, headers=person("ada")).status_code == 201
    for name, role in members:
        invited = api.post(
            f"/v1/organizations/{ORG}/invitations",
            json={"email": f"{name}@example.com", "roles": [role]},
            headers=_owner(),
        )
        assert invited.status_code == 201, invited.text
        accepted = api.post(
            "/v1/invitations/accept",
            json={"organization": ORG, "token": invited.json()["token"]},
            headers=person(name),
        )
        assert accepted.status_code == 200, accepted.text


def _set_up(api) -> None:
    app = api.put(
        "/v1/applications/browser",
        json={"name": "Browser extension", "guardrail": "gr-test"},
        headers=_owner(),
    )
    assert app.status_code == 201, app.text
    settings = api.put("/v1/browser/settings", json={"application_id": "browser"}, headers=_owner())
    assert settings.status_code == 200, settings.text


def _connect(api, who: str, label: str = "Chrome on Windows"):
    return api.post(
        "/v1/browser/connections", json={"device_label": label}, headers=person(who, tenant=ORG)
    )


def _evaluate(api, secret: str, text: str, app: str = "chatgpt.com"):
    body = {
        "contract_version": "0.14",
        "request_id": "br-6f1c2d3e4a5b46c78d9e0f1a2b3c4d5e",
        "timestamp": "2026-11-10T09:01:00Z",
        "phase": "PRE_LLM",
        "content": {
            "messages": [{"role": "user", "content": text}],
            "focus": "LAST_USER_MESSAGE",
            "content_type": "text",
        },
        "destination": {"app": app, "class": "external_llm"},
        "timeout_ms": 3000,
    }
    with TestClient(create_engine_app(api.engine_settings, detector=api.detector)) as engine:
        return engine.post("/v1/evaluate", json=body, headers={"Authorization": f"Bearer {secret}"})
    # leaving the block shuts the engine down: its records queue is flushed


# ------------------------------------------------------------------ settings


def test_settings_name_an_existing_application_and_only_admins_change_them(stack) -> None:
    _organization(stack, ("grace", "viewer"))
    viewer = person("grace", tenant=ORG)
    empty = stack.get("/v1/browser/settings", headers=viewer)
    assert empty.status_code == 200
    assert empty.json() == {
        "tenant_id": ORG,
        "browser": {"application_id": None},
        "application": None,
    }
    unknown = stack.put("/v1/browser/settings", json={"application_id": "nope"}, headers=_owner())
    assert unknown.status_code == 422 and _error(unknown) == "invalid_configuration"
    denied = stack.put("/v1/browser/settings", json={"application_id": None}, headers=viewer)
    assert denied.status_code == 403 and _error(denied) == "forbidden"
    _set_up(stack)
    view = stack.get("/v1/browser/settings", headers=viewer).json()
    assert view["browser"] == {"application_id": "browser"}
    assert view["application"] == {
        "application_id": "browser",
        "name": "Browser extension",
        "guardrail": "gr-test",
        "enabled": True,
    }
    # The browser application cannot be deleted while browsers use it.
    gone = stack.delete("/v1/applications/browser", headers=_owner())
    assert gone.status_code == 422 and _error(gone) == "invalid_configuration", gone.text
    assert "browser extension" in gone.json()["detail"]["detail"]


# ------------------------------------------------------------------ connections


def test_a_member_connects_their_own_browser_with_an_evaluate_only_key(stack) -> None:
    _organization(stack, ("grace", "viewer"))
    before = _connect(stack, "grace")
    assert before.status_code == 409 and _error(before) == "browser_not_set_up"
    _set_up(stack)
    made = _connect(stack, "grace")
    assert made.status_code == 201, made.text
    body = made.json()
    connection = body["connection"]
    assert body["secret"].startswith(f"umai_live_sh_{connection['key_id']}_")
    assert body["published"] is True
    assert connection["user"]["email"] == "grace@example.com"
    assert connection["user"]["name"] == "Grace"
    assert (connection["device_label"], connection["application_id"], connection["status"]) == (
        "Chrome on Windows",
        "browser",
        "active",
    )
    keys = stack.get(f"/v1/tenants/{ORG}/keys", headers=_owner()).json()["keys"]
    [key] = keys
    assert key["scopes"] == ["evaluate"]  # never the gateway or reads
    assert (key["client"], key["user_id"], key["name"]) == (
        "browser",
        connection["user"]["user_id"],
        "Browser · Chrome on Windows",
    )
    snapshot = json.loads((stack.engine_settings.key_registry_dir / f"{ORG}.json").read_text())
    assert snapshot["contract_version"] == "0.6"
    assert snapshot["keys"][0]["client"] == "browser"
    assert snapshot["keys"][0]["user_id"] == connection["user"]["user_id"]
    assert body["secret"] not in json.dumps(snapshot)


def test_connecting_needs_a_person_a_label_in_bounds_and_stops_at_the_limit(stack) -> None:
    _organization(stack)
    _set_up(stack)
    token_only = stack.post("/v1/browser/connections", headers={"X-Umai-Tenant": ORG})
    assert token_only.status_code == 403 and _error(token_only) == "actor_required"
    too_long = _connect(stack, "ada", label="x" * 121)
    assert too_long.status_code == 422
    unknown_field = stack.post(
        "/v1/browser/connections", json={"scopes": ["gateway"]}, headers=_owner()
    )
    assert unknown_field.status_code == 422
    default = stack.post("/v1/browser/connections", headers=_owner())
    assert default.status_code == 201 and default.json()["connection"]["device_label"] == "Browser"
    for _ in range(MAX_CONNECTIONS_PER_PERSON - 1):
        assert _connect(stack, "ada").status_code == 201
    over = _connect(stack, "ada")
    assert over.status_code == 409 and _error(over) == "too_many_connections"
    # A disconnected browser frees its place.
    first = default.json()["connection"]["key_id"]
    assert stack.delete(f"/v1/browser/connections/{first}", headers=_owner()).status_code == 200
    assert _connect(stack, "ada").status_code == 201


def test_members_see_and_revoke_their_own_admins_everyones(stack) -> None:
    _organization(stack, ("grace", "viewer"), ("alan", "viewer"))
    _set_up(stack)
    graces = _connect(stack, "grace").json()["connection"]["key_id"]
    alans = _connect(stack, "alan").json()["connection"]["key_id"]
    grace = person("grace", tenant=ORG)
    mine = stack.get("/v1/browser/connections", headers=grace).json()["connections"]
    assert [c["key_id"] for c in mine] == [graces]
    everyone = stack.get("/v1/browser/connections?scope=all", headers=grace)
    assert everyone.status_code == 403 and _error(everyone) == "forbidden"
    others = stack.delete(f"/v1/browser/connections/{alans}", headers=grace)
    assert others.status_code == 404  # not "forbidden": a member does not learn others' ids
    listed = stack.get("/v1/browser/connections?scope=all", headers=_owner()).json()["connections"]
    assert sorted(c["user"]["email"] for c in listed) == ["alan@example.com", "grace@example.com"]
    revoked = stack.delete(f"/v1/browser/connections/{alans}", headers=_owner())
    assert revoked.status_code == 200 and revoked.json()["connection"]["status"] == "revoked"
    again = stack.delete(f"/v1/browser/connections/{alans}", headers=_owner())
    assert again.status_code == 200 and again.json()["published"] is False  # idempotent
    own = stack.delete(f"/v1/browser/connections/{graces}", headers=grace)
    assert own.status_code == 200 and own.json()["connection"]["status"] == "revoked"
    # Ordinary keys are not browser connections, whoever asks.
    ordinary = stack.post(f"/v1/tenants/{ORG}/keys", json={"name": "sdk"}, headers=_owner())
    key_id = ordinary.json()["key"]["key_id"]
    assert stack.delete(f"/v1/browser/connections/{key_id}", headers=_owner()).status_code == 404

    async def audited() -> list[str]:
        engine = make_engine(stack.db_url)
        async with tenant_session(engine, await _tenant_uuid(engine)) as session:
            rows = (await session.scalars(select(AuditEventRow.action))).all()
        await engine.dispose()
        return list(rows)

    actions = asyncio.run(audited())
    assert actions.count("browser.connect") == 2
    assert actions.count("browser.disconnect") >= 2
    assert "browser.settings.update" in actions


async def _tenant_uuid(engine):
    from umai_decision_engine.db.repositories import TenantRepository

    return (await TenantRepository(engine).get(ORG)).id


def test_leaving_or_being_removed_revokes_the_persons_connections(stack) -> None:
    _organization(stack, ("grace", "viewer"), ("alan", "viewer"))
    _set_up(stack)
    _connect(stack, "grace")
    _connect(stack, "grace", label="Edge on Windows")
    _connect(stack, "alan")
    grace_id = stack.get("/v1/browser/connections", headers=person("grace", tenant=ORG)).json()[
        "connections"
    ][0]["user"]["user_id"]
    removed = stack.delete(f"/v1/organizations/{ORG}/members/{grace_id}", headers=_owner())
    assert removed.status_code == 204, removed.text
    left = stack.post(f"/v1/organizations/{ORG}/leave", headers=person("alan", tenant=ORG))
    assert left.status_code == 204, left.text
    listed = stack.get("/v1/browser/connections?scope=all", headers=_owner()).json()["connections"]
    assert len(listed) == 3 and {c["status"] for c in listed} == {"revoked"}
    snapshot = json.loads((stack.engine_settings.key_registry_dir / f"{ORG}.json").read_text())
    assert {k["status"] for k in snapshot["keys"]} == {"revoked"}


# ------------------------------------------------------------------ records


def test_the_engine_records_a_browser_call_with_the_person_and_the_site(stack) -> None:
    _organization(stack, ("grace", "viewer"))
    _set_up(stack)
    made = _connect(stack, "grace").json()
    secret, user_id = made["secret"], made["connection"]["user"]["user_id"]
    blocked = _evaluate(stack, secret, f"TC kimlik {TCKN} için dilekçe yaz")
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["decision"]["action"] == "BLOCK"
    allowed = _evaluate(stack, secret, "Bir toplantı gündemi yaz", app="claude.ai")
    assert allowed.status_code == 200 and allowed.json()["decision"]["action"] == "ALLOW"

    owner = _owner()
    page = stack.get("/v1/decision-events?source=browser", headers=owner).json()
    assert len(page["events"]) == 2
    assert {e["source"] for e in page["events"]} == {"browser"}
    assert {e["user_id"] for e in page["events"]} == {user_id}
    assert {e["application_id"] for e in page["events"]} == {"browser"}
    assert page["people"] == {user_id: {"email": "grace@example.com", "name": "Grace"}}
    assert TCKN not in json.dumps(page)
    by_site = stack.get("/v1/decision-events?destination_app=chatgpt.com", headers=owner).json()
    assert [e["decision"]["action"] for e in by_site["events"]] == ["BLOCK"]
    by_person = stack.get(f"/v1/decision-events?user_id={user_id}", headers=owner).json()
    assert len(by_person["events"]) == 2
    nobody = stack.get(
        "/v1/decision-events?user_id=00000000-0000-4000-8000-000000000000", headers=owner
    ).json()
    assert nobody["events"] == [] and nobody["people"] == {}
    detail = stack.get(f"/v1/decision-events/{by_site['events'][0]['event_id']}", headers=owner)
    assert detail.json()["people"][user_id]["email"] == "grace@example.com"
    summary = stack.get("/v1/decision-events/summary", headers=owner).json()
    assert summary["by_source"] == {"browser": 2}
    csv = stack.get("/v1/decision-events/export.csv?source=browser", headers=owner).text
    header, *rows = csv.strip().splitlines()
    assert header.split(",")[-1] == "user_id"
    assert all(row.endswith(user_id) for row in rows) and len(rows) == 2

    # Revoked: the engine refuses the key (a fresh engine loads the snapshot at start).
    key_id = made["connection"]["key_id"]
    assert stack.delete(f"/v1/browser/connections/{key_id}", headers=owner).status_code == 200
    refused = _evaluate(stack, secret, "Bir toplantı gündemi yaz")
    assert refused.status_code == 401


def test_the_operator_reads_settings_and_every_connection_but_never_connects(stack) -> None:
    _organization(stack)
    _set_up(stack)
    assert _connect(stack, "ada").status_code == 201
    operator = {"X-Umai-Tenant": ORG}  # the control token alone
    settings = stack.get("/v1/browser/settings", headers=operator)
    assert settings.status_code == 200 and settings.json()["browser"]["application_id"] == "browser"
    everyone = stack.get("/v1/browser/connections?scope=all", headers=operator)
    assert everyone.status_code == 200 and len(everyone.json()["connections"]) == 1
    mine = stack.get("/v1/browser/connections", headers=operator)
    assert mine.status_code == 403 and _error(mine) == "actor_required"
