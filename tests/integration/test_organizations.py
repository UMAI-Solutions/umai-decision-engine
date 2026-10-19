"""Organizations end to end in the policy service (ADR-0033, contract organizations 0.2):
creation and its guards, the starter guardrail, invitations, roles, the last owner and tenant
binding. Persons are synthetic; assertions are signed as the console signs them."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from tests.conftest import bundle_yaml
from umai_decision_engine.contracts.credentials import ActorAssertion
from umai_decision_engine.control_plane.actors import public_key_b64, sign_actor_assertion
from umai_decision_engine.control_plane.app import create_app
from umai_decision_engine.db.models import Invitation, Tenant
from umai_decision_engine.db.session import global_session, make_engine, tenant_session
from umai_decision_engine.settings import Settings

CONTROL = "control-token"
KEY = ec.generate_private_key(ec.SECP256R1())
# The same flows on PostgreSQL with row-level security when a test server is configured (CI).
ADMIN_URL = os.environ.get("UMAI_DE_TEST_POSTGRES_ADMIN_URL")
APP_URL = os.environ.get("UMAI_DE_TEST_POSTGRES_APP_URL")


def _reset_postgres() -> None:
    async def reset() -> None:
        admin = make_engine(ADMIN_URL)
        async with admin.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.execute(text("GRANT USAGE ON SCHEMA public TO umai_app"))
        await admin.dispose()

    asyncio.run(reset())


def person(
    sub: str, email: str | None = None, *, tenant: str | None = None, verified: bool = True
) -> dict[str, str]:
    now = int(time.time())
    claims = ActorAssertion(
        iss="umai-console",
        aud="umai-policy-service",
        sub=f"auth0|{sub}",
        email=email or f"{sub}@example.com",
        email_verified=verified,
        name=sub.title(),
        tenant_id=tenant,
        session_id=f"s-{sub}",
        iat=now,
        exp=now + 120,
        jti=f"j-{sub}-{now}-{tenant}",
    )
    return {"X-Umai-Actor": sign_actor_assertion(claims, KEY, "console-1")}


def _org(name: str = "Acme Security", org_id: str = "acme-security") -> dict:
    return {"name": name, "id": org_id, "region": "sh", "accept_terms": True, "terms_version": "1"}


@pytest.fixture(params=["sqlite", "postgres"])
def api(request, write_bundle, detector, tmp_path):
    database = {}
    if request.param == "postgres":
        if not (ADMIN_URL and APP_URL):
            pytest.skip("no test Postgres configured")
        _reset_postgres()
        database = {"database_url": APP_URL, "database_admin_url": ADMIN_URL}
    write_bundle(bundle_yaml(), active=1)  # the template: gr-test of the default tenant
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
    )
    with TestClient(create_app(settings, detector=detector)) as client:
        client.headers["Authorization"] = f"Bearer {CONTROL}"
        client.root = write_bundle.root
        yield client


def _error(response) -> str:
    return response.json()["detail"]["error"]


def _create(api, who: str = "ada", **body) -> dict:
    response = api.post("/v1/organizations", json=_org(**body), headers=person(who))
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------ creation


def test_create_organization_makes_the_creator_owner_with_a_starter_guardrail(api) -> None:
    created = _create(api)
    assert created["organization"]["id"] == "acme-security"
    assert created["organization"]["edition"] == "community"
    assert created["starter_guardrail"] == {"id": "gr-test", "version": 1}
    manifest = api.root / "tenants" / "acme-security" / "gr-test" / "1" / "manifest.json"
    assert json.loads(manifest.read_text())["tenant_id"] == "acme-security"
    mine = api.get("/v1/me/organizations", headers=person("ada")).json()["organizations"]
    assert mine == [
        {
            "id": "acme-security",
            "name": "Acme Security",
            "region": "sh",
            "status": "active",
            "roles": ["owner"],
        }
    ]
    owner = person("ada", tenant="acme-security")
    guardrails = api.get("/v1/guardrails", headers=owner).json()["guardrails"]
    assert [(g["id"], g["active_version"]) for g in guardrails] == [("gr-test", 1)]
    history = api.get("/v1/guardrails/gr-test", headers=owner).json()["history"]
    assert history[-1]["actor"] == {"type": "person", "sub": "auth0|ada"}


@pytest.mark.parametrize(
    ("headers", "body", "status", "code"),
    [
        (person("ada", verified=False), _org(), 403, "email_unverified"),
        (person("ada", "ada@mailinator.com"), _org(), 422, "disposable_email_domain"),
        (person("ada"), {**_org(), "region": "eu"}, 422, "region_unavailable"),
        (person("ada"), _org(org_id="console"), 409, "id_taken"),
    ],
)
def test_creation_guards(api, headers, body, status, code) -> None:
    response = api.post("/v1/organizations", json=body, headers=headers)
    assert response.status_code == status and _error(response) == code


def test_one_community_organization_per_person_and_unique_ids(api) -> None:
    _create(api)
    second = api.post("/v1/organizations", json=_org("Other", "other-org"), headers=person("ada"))
    assert second.status_code == 403 and _error(second) == "community_limit_reached"
    taken = api.post("/v1/organizations", json=_org(), headers=person("grace"))
    assert taken.status_code == 409 and _error(taken) == "id_taken"


def test_the_operator_token_cannot_create_an_organization(api) -> None:
    response = api.post("/v1/organizations", json=_org())
    assert response.status_code == 403 and _error(response) == "actor_required"


# ------------------------------------------------------------ invitations


def _invite(api, roles=("viewer",), email="grace@example.com", inviter="ada") -> str:
    response = api.post(
        "/v1/organizations/acme-security/invitations",
        json={"email": email, "roles": list(roles)},
        headers=person(inviter, tenant="acme-security"),
    )
    assert response.status_code == 201, response.text
    assert response.json()["invitation"]["state"] == "pending"
    return response.json()["token"]


def _accept(api, token: str, who: str = "grace", **kw):
    return api.post(
        "/v1/invitations/accept",
        json={"organization": "acme-security", "token": token},
        headers=person(who, **kw),
    )


def test_invitation_is_accepted_only_by_its_verified_addressee_once(api) -> None:
    _create(api)
    token = _invite(api)
    wrong = _accept(api, token, who="mallory")
    assert wrong.status_code == 403 and _error(wrong) == "invitation_email_mismatch"
    unverified = _accept(api, token, verified=False)
    assert unverified.status_code == 403 and _error(unverified) == "email_unverified"
    accepted = _accept(api, token)
    assert accepted.status_code == 200 and accepted.json()["roles"] == ["viewer"]
    again = _accept(api, token)
    assert again.status_code == 409 and _error(again) == "invitation_used"
    members = api.get(
        "/v1/organizations/acme-security/members", headers=person("ada", tenant="acme-security")
    ).json()["members"]
    assert sorted((m["user"]["email"], m["roles"]) for m in members) == [
        ("ada@example.com", ["owner"]),
        ("grace@example.com", ["viewer"]),
    ]
    # Times carry their zone on SQLite too, or the console shows them shifted to local time.
    assert all(datetime.fromisoformat(m["joined_at"]).utcoffset() == timedelta(0) for m in members)
    grace = api.get("/v1/me/organizations", headers=person("grace")).json()["organizations"]
    assert [o["id"] for o in grace] == ["acme-security"]


def test_revoked_resent_and_expired_invitations(api) -> None:
    _create(api)
    owner = person("ada", tenant="acme-security")
    first = _invite(api)
    listed = api.get("/v1/organizations/acme-security/invitations", headers=owner).json()
    invitation_id = listed["invitations"][0]["id"]
    resent = api.post(
        f"/v1/organizations/acme-security/invitations/{invitation_id}/resend", headers=owner
    )
    assert resent.status_code == 201
    old = _accept(api, first)
    assert old.status_code == 410 and _error(old) == "invitation_revoked"
    assert _accept(api, resent.json()["token"]).status_code == 200

    expiring = _invite(api, email="lin@example.com")

    async def expire() -> None:
        db = api.app.state.db
        async with global_session(db) as session:
            tenant_id = await session.scalar(
                select(Tenant.id).where(Tenant.slug == "acme-security")
            )
        async with tenant_session(db, tenant_id) as session:  # RLS on Postgres
            await session.execute(
                update(Invitation)
                .where(Invitation.tenant_id == tenant_id, Invitation.email == "lin@example.com")
                .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
            )

    api.portal.call(expire)
    expired = _accept(api, expiring, who="lin")
    assert expired.status_code == 410 and _error(expired) == "invitation_expired"


def test_roles_offered_in_community_and_who_may_invite(api) -> None:
    _create(api)
    response = api.post(
        "/v1/organizations/acme-security/invitations",
        json={"email": "x@example.com", "roles": ["policy_editor"]},
        headers=person("ada", tenant="acme-security"),
    )
    assert response.status_code == 422 and _error(response) == "role_unavailable"
    _accept(api, _invite(api))  # grace joins as viewer
    viewer = person("grace", tenant="acme-security")
    assert (
        api.post(
            "/v1/organizations/acme-security/invitations",
            json={"email": "x@example.com", "roles": ["viewer"]},
            headers=viewer,
        ).status_code
        == 403
    )
    assert api.get("/v1/organizations/acme-security/members", headers=viewer).status_code == 403
    assert api.get("/v1/organizations/acme-security", headers=viewer).status_code == 200


# ------------------------------------------------------------ roles and the last owner


def _user_id(api, email: str) -> str:
    members = api.get(
        "/v1/organizations/acme-security/members", headers=person("ada", tenant="acme-security")
    ).json()["members"]
    return next(m["user"]["id"] for m in members if m["user"]["email"] == email)


def test_last_owner_and_ownership_rules(api) -> None:
    _create(api)
    owner = person("ada", tenant="acme-security")
    ada = _user_id(api, "ada@example.com")
    leave = api.post("/v1/organizations/acme-security/leave", headers=owner)
    assert leave.status_code == 409 and _error(leave) == "last_owner"
    demote = api.patch(
        f"/v1/organizations/acme-security/members/{ada}", json={"roles": ["admin"]}, headers=owner
    )
    assert demote.status_code == 409 and _error(demote) == "last_owner"

    _accept(api, _invite(api, roles=("admin",)))  # grace joins as admin
    grace = _user_id(api, "grace@example.com")
    admin = person("grace", tenant="acme-security")
    self_promote = api.patch(
        f"/v1/organizations/acme-security/members/{grace}",
        json={"roles": ["owner"]},
        headers=admin,
    )
    assert self_promote.status_code == 403  # only an owner grants ownership
    remove_owner = api.delete(f"/v1/organizations/acme-security/members/{ada}", headers=admin)
    assert remove_owner.status_code == 403

    promoted = api.patch(
        f"/v1/organizations/acme-security/members/{grace}",
        json={"roles": ["owner"]},
        headers=owner,
    )
    assert promoted.status_code == 200 and promoted.json()["roles"] == ["owner"]
    assert api.post("/v1/organizations/acme-security/leave", headers=owner).status_code == 204
    assert api.get("/v1/me/organizations", headers=person("ada")).json()["organizations"] == []


# ------------------------------------------------------------ tenant binding


def test_a_person_never_reaches_another_organization(api) -> None:
    _create(api)
    _create(api, who="grace", name="Globex", org_id="globex")
    ada_in_acme = person("ada", tenant="acme-security")
    response = api.get("/v1/organizations/globex", headers=ada_in_acme)
    assert response.status_code == 403 and _error(response) == "tenant_mismatch"
    ada_claims_globex = person("ada", tenant="globex")  # no membership there
    assert api.get("/v1/organizations/globex", headers=ada_claims_globex).status_code == 403
    assert api.get("/v1/guardrails", headers=ada_claims_globex).status_code == 403
    keys = api.get("/v1/tenants/globex/keys", headers=ada_in_acme)
    assert keys.status_code == 403


def test_owner_creates_the_first_api_key_for_their_organization(api) -> None:
    _create(api)
    response = api.post(
        "/v1/tenants/acme-security/keys",
        json={"name": "Default key"},
        headers=person("ada", tenant="acme-security"),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["key"]["created_by"] == "person:auth0|ada"
    assert body["secret"].startswith("umai_live_sh_") and body["published"] is True
