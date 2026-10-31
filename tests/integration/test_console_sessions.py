"""Console sessions in the policy service (ADR-0024 §2 amendment, contract console-sessions 0.1):
start, list, end one, end the others, sign-out, the per-person limit, idle and absolute ends,
the middleware's check with membership, and the assertion check on every call. Persons and
session ids are synthetic; assertions are signed as the console signs them."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

from tests.conftest import bundle_yaml
from umai_decision_engine.contracts.credentials import ActorAssertion
from umai_decision_engine.control_plane.actors import public_key_b64, sign_actor_assertion
from umai_decision_engine.control_plane.app import create_app
from umai_decision_engine.db.models import ConsoleSessionRow, Membership, Tenant
from umai_decision_engine.db.session import global_session, make_engine, tenant_session
from umai_decision_engine.db.sessions import session_hash
from umai_decision_engine.settings import Settings

CONTROL = "control-token"
KEY = ec.generate_private_key(ec.SECP256R1())
ADMIN_URL = os.environ.get("UMAI_DE_TEST_POSTGRES_ADMIN_URL")
APP_URL = os.environ.get("UMAI_DE_TEST_POSTGRES_APP_URL")
ORG = "acme-security"


def _reset_postgres() -> None:
    async def reset() -> None:
        admin = make_engine(ADMIN_URL)
        async with admin.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.execute(text("GRANT USAGE ON SCHEMA public TO umai_app"))
        await admin.dispose()

    asyncio.run(reset())


def new_sid() -> str:
    return secrets.token_urlsafe(32)  # as the console makes it: 256 bits, base64url


def person(sub: str, sid: str, *, tenant: str | None = None) -> dict[str, str]:
    now = int(time.time())
    claims = ActorAssertion(
        iss="umai-console",
        aud="umai-policy-service",
        sub=f"auth0|{sub}",
        email=f"{sub}@example.com",
        email_verified=True,
        name=sub.title(),
        tenant_id=tenant,
        session_id=sid,
        iat=now,
        exp=now + 120,
        jti=uuid.uuid4().hex,
    )
    return {"X-Umai-Actor": sign_actor_assertion(claims, KEY, "console-1")}


def _client(write_bundle, detector, tmp_path, database: dict, **overrides) -> TestClient:
    write_bundle(bundle_yaml(), active=1)
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
        **{"console_sessions": "required", **overrides},
    )
    return TestClient(create_app(settings, detector=detector))


@pytest.fixture(params=["sqlite", "postgres"])
def database(request) -> dict:
    if request.param == "postgres":
        if not (ADMIN_URL and APP_URL):
            pytest.skip("no test Postgres configured")
        _reset_postgres()
        return {"database_url": APP_URL, "database_admin_url": ADMIN_URL}
    return {}


@pytest.fixture
def make_api(write_bundle, detector, tmp_path, database):
    clients = []

    def make(**overrides) -> TestClient:
        client = _client(write_bundle, detector, tmp_path, database, **overrides)
        client.__enter__()
        client.headers["Authorization"] = f"Bearer {CONTROL}"
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def api(make_api) -> TestClient:
    return make_api()


def _error(response) -> str:
    return response.json()["detail"]["error"]


def _start(api, who: str, device: str | None = "Firefox on Windows") -> str:
    sid = new_sid()
    response = api.post("/v1/me/sessions", json={"device": device}, headers=person(who, sid))
    assert response.status_code == 201, response.text
    return sid


def _mine(api, who: str, sid: str) -> list[dict]:
    response = api.get("/v1/me/sessions", headers=person(who, sid))
    assert response.status_code == 200, response.text
    return response.json()["sessions"]


def _check(api, sid: str, who: str = "ada", tenant: str | None = None) -> dict:
    body = {"session_id": sid, "subject": f"auth0|{who}"}
    if tenant:
        body["tenant_id"] = tenant
    response = api.post("/v1/sessions/check", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _age(api, sid: str, **values) -> None:
    """Moves a session's times (idle and absolute ends) without waiting."""

    async def move() -> None:
        async with global_session(api.app.state.db) as session:
            await session.execute(
                update(ConsoleSessionRow)
                .where(ConsoleSessionRow.sid_sha256 == session_hash(sid))
                .values(**values)
            )

    api.portal.call(move)


def _row(api, sid: str) -> ConsoleSessionRow:
    async def read() -> ConsoleSessionRow:
        async with global_session(api.app.state.db) as session:
            return await session.scalar(
                select(ConsoleSessionRow).where(ConsoleSessionRow.sid_sha256 == session_hash(sid))
            )

    return api.portal.call(read)


# ------------------------------------------------------------ start, list, end


def test_start_records_the_hash_and_lists_the_current_session(api) -> None:
    sid = _start(api, "ada")
    row = _row(api, sid)
    assert row.sid_sha256 == session_hash(sid) and sid not in row.sid_sha256
    assert row.expires_at - row.created_at == timedelta(hours=12)
    (listed,) = _mine(api, "ada", sid)
    assert listed["current"] is True and listed["device"] == "Firefox on Windows"
    assert set(listed) == {"id", "device", "created_at", "last_seen_at", "expires_at", "current"}
    assert sid not in str(listed)  # the session id is never in a response


def test_a_second_device_is_listed_and_marked_current_only_for_itself(api) -> None:
    laptop = _start(api, "ada")
    phone = _start(api, "ada", device="Safari on iOS")
    from_laptop = {s["device"]: s["current"] for s in _mine(api, "ada", laptop)}
    assert from_laptop == {"Firefox on Windows": True, "Safari on iOS": False}
    assert [s["current"] for s in _mine(api, "ada", phone)].count(True) == 1
    assert _mine(api, "grace", _start(api, "grace"))[0]["device"] == "Firefox on Windows"
    assert len(_mine(api, "ada", laptop)) == 2  # grace's session is not ada's


def test_starting_the_same_session_twice_is_a_conflict(api) -> None:
    sid = _start(api, "ada")
    again = api.post("/v1/me/sessions", json={}, headers=person("ada", sid))
    assert again.status_code == 409 and _error(again) == "session_exists"


def test_a_device_label_outside_the_pattern_is_refused(api) -> None:
    sid = new_sid()
    agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/131.0 <script>"
    response = api.post("/v1/me/sessions", json={"device": agent}, headers=person("ada", sid))
    assert response.status_code == 422


def test_ending_one_session_stops_its_calls_at_once(api) -> None:
    laptop = _start(api, "ada")
    phone = _start(api, "ada", device="Safari on iOS")
    phone_id = next(s["id"] for s in _mine(api, "ada", laptop) if not s["current"])
    ended = api.delete(f"/v1/me/sessions/{phone_id}", headers=person("ada", laptop))
    assert ended.status_code == 204
    refused = api.get("/v1/me/organizations", headers=person("ada", phone))
    assert refused.status_code == 401 and _error(refused) == "session_ended"
    assert _check(api, phone) == {"state": "ended", "member": None}
    assert _row(api, phone).revoke_reason == "revoked"
    assert api.get("/v1/me/organizations", headers=person("ada", laptop)).status_code == 200


def test_sign_out_other_devices_keeps_this_one(api) -> None:
    here = _start(api, "ada")
    others = [_start(api, "ada", device="Chrome on Android") for _ in range(2)]
    response = api.post("/v1/me/sessions/revoke-others", headers=person("ada", here))
    assert response.status_code == 200 and response.json() == {"revoked": 2}
    assert [s["current"] for s in _mine(api, "ada", here)] == [True]
    for sid in others:
        assert _check(api, sid)["state"] == "ended"
        assert _row(api, sid).revoke_reason == "signed_out_elsewhere"


def test_sign_out_ends_the_record(api) -> None:
    sid = _start(api, "ada")
    assert api.delete("/v1/me/sessions/current", headers=person("ada", sid)).status_code == 204
    assert _check(api, sid)["state"] == "ended"
    assert _row(api, sid).revoke_reason == "signed_out"
    after = api.get("/v1/me/sessions", headers=person("ada", sid))
    assert after.status_code == 401 and _error(after) == "session_ended"


def test_nobody_ends_another_persons_session(api) -> None:
    ada = _start(api, "ada")
    grace = _start(api, "grace")
    grace_id = _mine(api, "grace", grace)[0]["id"]
    response = api.delete(f"/v1/me/sessions/{grace_id}", headers=person("ada", ada))
    assert response.status_code == 404 and _error(response) == "not_found"
    assert _check(api, grace, who="grace")["state"] == "active"


def test_beyond_the_limit_the_least_recently_seen_session_ends(make_api) -> None:
    api = make_api(console_session_limit=2)
    oldest = _start(api, "ada")
    _age(api, oldest, last_seen_at=datetime.now(UTC) - timedelta(minutes=30))
    second = _start(api, "ada")
    third = _start(api, "ada")
    assert _check(api, oldest)["state"] == "ended"
    assert _row(api, oldest).revoke_reason == "limit"
    assert {_check(api, second)["state"], _check(api, third)["state"]} == {"active"}


# ------------------------------------------------------------ ends by time


def test_idle_and_absolute_ends(api) -> None:
    idle = _start(api, "ada")
    _age(api, idle, last_seen_at=datetime.now(UTC) - timedelta(minutes=71))
    assert _check(api, idle)["state"] == "ended"
    refused = api.get("/v1/me/organizations", headers=person("ada", idle))
    assert refused.status_code == 401 and _error(refused) == "session_ended"

    old = _start(api, "ada")
    _age(api, old, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert _check(api, old)["state"] == "ended"
    assert len(_mine(api, "ada", _start(api, "ada"))) == 1  # ended sessions are not listed


def test_last_seen_is_written_at_most_every_five_minutes(api) -> None:
    sid = _start(api, "ada")
    first = _row(api, sid).last_seen_at
    _check(api, sid)
    assert _row(api, sid).last_seen_at == first
    _age(api, sid, last_seen_at=first - timedelta(minutes=6))
    _check(api, sid)
    assert _row(api, sid).last_seen_at > first - timedelta(minutes=1)


def test_records_are_deleted_thirty_days_after_they_expire(api) -> None:
    gone = _start(api, "ada")
    _age(api, gone, expires_at=datetime.now(UTC) - timedelta(days=31))
    kept = _start(api, "ada")
    _age(api, kept, expires_at=datetime.now(UTC) - timedelta(days=29))
    _start(api, "grace")  # any start purges
    assert _row(api, gone) is None and _row(api, kept) is not None


# ------------------------------------------------------------ the check and organizations


def _org(api, sid: str) -> None:
    body = {"name": "Acme", "id": ORG, "region": "sh", "accept_terms": True, "terms_version": "1"}
    assert api.post("/v1/organizations", json=body, headers=person("ada", sid)).status_code == 201


def _join(api, owner_sid: str, who: str) -> str:
    """``who`` joins the organization through an invitation; returns their session id."""

    invite = api.post(
        f"/v1/organizations/{ORG}/invitations",
        json={"email": f"{who}@example.com", "roles": ["viewer"]},
        headers=person("ada", owner_sid, tenant=ORG),
    )
    assert invite.status_code == 201, invite.text
    sid = _start(api, who)
    accepted = api.post(
        "/v1/invitations/accept",
        json={"organization": ORG, "token": invite.json()["token"]},
        headers=person(who, sid),
    )
    assert accepted.status_code == 200, accepted.text
    return sid


def _member_id(api, owner_sid: str, email: str) -> str:
    members = api.get(
        f"/v1/organizations/{ORG}/members", headers=person("ada", owner_sid, tenant=ORG)
    ).json()["members"]
    return next(m["user"]["id"] for m in members if m["user"]["email"] == email)


def test_check_answers_unknown_ended_and_another_persons_session(api) -> None:
    assert _check(api, new_sid()) == {"state": "unknown", "member": None}
    sid = _start(api, "ada")
    assert _check(api, sid) == {"state": "active", "member": None}
    assert _check(api, sid, who="mallory")["state"] == "ended"  # the cookie's subject differs


def test_a_removed_member_loses_the_organization_within_the_check(api) -> None:
    owner = _start(api, "ada")
    _org(api, owner)
    grace = _join(api, owner, "grace")
    assert _check(api, grace, who="grace", tenant=ORG) == {"state": "active", "member": True}
    grace_id = _member_id(api, owner, "grace@example.com")
    removed = api.delete(
        f"/v1/organizations/{ORG}/members/{grace_id}", headers=person("ada", owner, tenant=ORG)
    )
    assert removed.status_code == 204
    # Still signed in (her session is hers), no longer in the organization.
    assert _check(api, grace, who="grace", tenant=ORG) == {"state": "active", "member": False}
    refused = api.get(f"/v1/organizations/{ORG}", headers=person("grace", grace, tenant=ORG))
    assert refused.status_code == 403  # policy-service calls in it fail at once


def test_a_suspended_membership_or_organization_is_not_a_membership(api) -> None:
    owner = _start(api, "ada")
    _org(api, owner)
    grace = _join(api, owner, "grace")
    grace_id = uuid.UUID(_member_id(api, owner, "grace@example.com"))

    async def suspend_membership() -> None:
        db = api.app.state.db
        async with global_session(db) as session:
            tenant_id = await session.scalar(select(Tenant.id).where(Tenant.slug == ORG))
        async with tenant_session(db, tenant_id) as session:
            await session.execute(
                update(Membership)
                .where(Membership.tenant_id == tenant_id, Membership.user_id == grace_id)
                .values(state="suspended")
            )

    api.portal.call(suspend_membership)
    assert _check(api, grace, who="grace", tenant=ORG)["member"] is False
    assert _check(api, owner, tenant=ORG)["member"] is True

    async def suspend_organization() -> None:
        async with global_session(api.app.state.db) as session:
            await session.execute(
                update(Tenant).where(Tenant.slug == ORG).values(status="suspended")
            )

    api.portal.call(suspend_organization)
    assert _check(api, owner, tenant=ORG)["member"] is False


# ------------------------------------------------------------ modes and the permission matrix


def test_required_mode_refuses_an_assertion_without_a_record(api) -> None:
    refused = api.get("/v1/me/organizations", headers=person("ada", new_sid()))
    assert refused.status_code == 401 and _error(refused) == "session_ended"


def test_optional_mode_allows_an_unrecorded_session_but_not_an_ended_one(make_api) -> None:
    api = make_api(console_sessions="optional")
    assert api.get("/v1/me/organizations", headers=person("ada", new_sid())).status_code == 200
    sid = _start(api, "ada")
    assert api.delete("/v1/me/sessions/current", headers=person("ada", sid)).status_code == 204
    refused = api.get("/v1/me/organizations", headers=person("ada", sid))
    assert refused.status_code == 401 and _error(refused) == "session_ended"


def test_the_check_is_the_consoles_and_starting_is_a_persons(api) -> None:
    sid = _start(api, "ada")
    body = {"session_id": sid, "subject": "auth0|ada"}
    as_person = api.post("/v1/sessions/check", json=body, headers=person("ada", sid))
    assert as_person.status_code == 403
    as_operator = api.post("/v1/me/sessions", json={})
    assert as_operator.status_code == 403 and _error(as_operator) == "actor_required"
    short = api.post("/v1/sessions/check", json={"session_id": "short", "subject": "auth0|ada"})
    assert short.status_code == 422
    unauthenticated = api.post("/v1/sessions/check", json=body, headers={"Authorization": ""})
    assert unauthenticated.status_code == 401


def test_session_ids_never_reach_the_logs(api, caplog) -> None:
    caplog.set_level(logging.INFO)
    sid = _start(api, "ada")
    api.post("/v1/me/sessions/revoke-others", headers=person("ada", sid))
    api.delete("/v1/me/sessions/current", headers=person("ada", sid))
    api.get("/v1/me/sessions", headers=person("ada", sid))
    assert sid not in caplog.text and session_hash(sid) not in caplog.text
    assert "session.started" in caplog.text and "session.refused" in caplog.text


def test_one_row_per_sign_in(api) -> None:
    for _ in range(3):
        _start(api, "ada")

    async def count() -> int:
        async with global_session(api.app.state.db) as session:
            return await session.scalar(select(func.count()).select_from(ConsoleSessionRow))

    assert api.portal.call(count) == 3
