"""Policy service with on-behalf-of assertions required (ADR-0023 §1, ADR-0029): who may publish,
activate and manage keys, in which tenant, and what the change log records. Synthetic data."""

from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from umai_decision_engine.contracts.credentials import ActorAssertion
from umai_decision_engine.control_plane.actors import public_key_b64, sign_actor_assertion
from umai_decision_engine.control_plane.app import create_app
from umai_decision_engine.settings import Settings

CONTROL = "control-token"
KEY = ec.generate_private_key(ec.SECP256R1())
_CURRENT: dict[str, TestClient] = {}


def _seed(client: TestClient, sub: str, tenant_slug: str, roles: tuple[str, ...]) -> None:
    """Roles live in the tenant store (ADR-0033 §1): give the person a membership there."""

    state = client.app.state

    async def go() -> None:
        tenant = await state.tenants.get(tenant_slug)
        orgs = state.organizations
        user = await orgs.users.upsert(sub, f"{sub.split('|')[-1]}@example.com", True, None)
        if tenant is not None and roles and not await orgs.memberships.roles(tenant, user):
            await orgs.memberships.add(tenant, user, list(roles))

    client.portal.call(go)


def _assertion(tenant: str | None = "default", roles=("policy_editor",), sub="auth0|ada") -> str:
    """A signed assertion for a person whose membership (with ``roles``) is in the store. The
    claim's own ``roles`` are deliberately different: the service must not believe them."""

    if tenant is not None:
        _seed(_CURRENT["client"], sub, tenant, tuple(roles))
    now = int(time.time())
    claims = ActorAssertion(
        iss="umai-console",
        aud="umai-policy-service",
        sub=sub,
        email=f"{sub.split('|')[-1]}@example.com",
        email_verified=True,
        tenant_id=tenant,
        roles=["owner"] if tenant else [],  # ignored since credentials 0.3
        session_id="s-1",
        iat=now,
        exp=now + 120,
        jti=f"j-{now}",
    )
    return sign_actor_assertion(claims, KEY, "console-1")


@pytest.fixture
def client(write_bundle, detector, tmp_path):
    write_bundle(bundle_yaml(), active=1)
    settings = Settings(
        env="test",
        require_signed_bundles=False,
        policy_dir=write_bundle.root,
        control_db_path=tmp_path / "control.sqlite",
        control_api_token=CONTROL,
        actor_assertion="required",
        actor_public_keys={"console-1": public_key_b64(KEY)},
    )
    with TestClient(create_app(settings, detector=detector)) as c:
        c.headers["Authorization"] = f"Bearer {CONTROL}"
        _CURRENT["client"] = c
        yield c


@pytest.fixture
def gateway_client(write_bundle, detector, tmp_path):
    write_bundle(bundle_yaml(), active=1)
    settings = Settings(
        env="test",
        require_signed_bundles=False,
        policy_dir=write_bundle.root,
        control_db_path=tmp_path / "control.sqlite",
        control_api_token=CONTROL,
        actor_assertion="required",
        actor_public_keys={"console-1": public_key_b64(KEY)},
        applications_dir=tmp_path / "applications",
    )
    with TestClient(create_app(settings, detector=detector)) as c:
        c.headers["Authorization"] = f"Bearer {CONTROL}"
        _CURRENT["client"] = c
        yield c


def _as(token: str) -> dict[str, str]:
    return {"X-Umai-Actor": token}


def test_operator_may_run_operator_actions_only(client) -> None:
    assert client.get("/v1/tenants").status_code == 200
    assert client.get("/v1/guardrails").status_code == 200  # reads are operator actions
    response = client.post("/v1/guardrails/gr-test/activate", json={"version": 1})
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "actor_required"


def test_policy_editor_publishes_and_the_change_log_names_them(client) -> None:
    editor = _as(_assertion())
    assert client.post("/v1/guardrails/gr-test/draft", json={}, headers=editor).status_code == 201
    published = client.post("/v1/guardrails/gr-test/draft/publish", json={}, headers=editor)
    assert published.status_code == 200, published.text
    history = client.get("/v1/guardrails/gr-test", headers=editor).json()["history"]
    assert history[-1]["action"] == "publish"
    assert history[-1]["actor"] == {"type": "person", "sub": "auth0|ada"}


def test_policy_editor_cannot_activate_but_approver_can(client) -> None:
    editor = _as(_assertion())
    response = client.post("/v1/guardrails/gr-test/activate", json={"version": 1}, headers=editor)
    assert response.status_code == 403 and response.json()["detail"]["error"] == "forbidden"
    approver = _as(_assertion(roles=("approver",), sub="auth0|grace"))
    response = client.post("/v1/guardrails/gr-test/activate", json={"version": 1}, headers=approver)
    assert response.status_code == 200
    history = client.get("/v1/guardrails/gr-test", headers=approver).json()["history"]
    assert history[-1]["actor"] == {"type": "person", "sub": "auth0|grace"}


def test_publish_and_activate_together_needs_both_permissions(client) -> None:
    editor = _as(_assertion())
    client.post("/v1/guardrails/gr-test/draft", json={}, headers=editor)
    response = client.post(
        "/v1/guardrails/gr-test/draft/publish", json={"activate": True}, headers=editor
    )
    assert response.status_code == 403


def test_viewer_reads_but_cannot_edit(client) -> None:
    viewer = _as(_assertion(roles=("viewer",)))
    assert client.get("/v1/guardrails", headers=viewer).status_code == 200
    response = client.post("/v1/guardrails/gr-test/draft", json={}, headers=viewer)
    assert response.status_code == 403


def test_community_admin_may_publish(client) -> None:
    admin = _as(_assertion(roles=("admin",)))
    client.post("/v1/guardrails/gr-test/draft", json={}, headers=admin)
    assert (
        client.post("/v1/guardrails/gr-test/draft/publish", json={}, headers=admin).status_code
        == 200
    )


def test_person_is_bound_to_their_tenant(client) -> None:
    client.post("/v1/tenants", json={"id": "other"})
    stranger = _as(_assertion(tenant="other", roles=("owner",)))
    response = client.get("/v1/guardrails", headers={**stranger, "X-Umai-Tenant": "default"})
    assert response.status_code == 403 and response.json()["detail"]["error"] == "tenant_mismatch"
    keys = client.get("/v1/tenants/default/keys", headers=stranger)
    assert keys.status_code == 403 and keys.json()["detail"]["error"] == "tenant_mismatch"
    # in their own tenant they see their own (empty) guardrails, never the default tenant's
    assert client.get("/v1/guardrails", headers=stranger).json()["guardrails"] == []


def test_person_without_tenant_cannot_act_in_one(client) -> None:
    response = client.get("/v1/guardrails", headers=_as(_assertion(tenant=None)))
    assert response.status_code == 403


def test_people_cannot_create_tenants(client) -> None:
    owner = _as(_assertion(roles=("owner",)))
    assert client.post("/v1/tenants", json={"id": "x1"}, headers=owner).status_code == 403


def test_owner_creates_a_key_attributed_to_them(client) -> None:
    owner = _as(_assertion(roles=("owner",), sub="auth0|lin"))
    response = client.post("/v1/tenants/default/keys", json={"name": "sdk"}, headers=owner)
    assert response.status_code == 201
    assert response.json()["key"]["created_by"] == "person:auth0|lin"


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b.c"])
def test_invalid_assertion_is_401(client, token: str) -> None:
    response = client.get("/v1/guardrails", headers=_as(token))
    assert response.status_code == 401 and response.json()["detail"]["error"] == "invalid_actor"


def test_assertion_never_replaces_the_service_credential(client) -> None:
    response = client.get(
        "/v1/guardrails", headers={"Authorization": "Bearer wrong", **_as(_assertion())}
    )
    assert response.status_code == 401


def test_claimed_roles_are_not_believed(client) -> None:
    """A person without a membership gets nothing, whatever roles the assertion claims."""

    now = int(time.time())
    claims = ActorAssertion(
        iss="umai-console",
        aud="umai-policy-service",
        sub="auth0|mallory",
        tenant_id="default",
        roles=["owner"],
        session_id="s-9",
        iat=now,
        exp=now + 60,
        jti="j-m",
    )
    token = sign_actor_assertion(claims, KEY, "console-1")
    assert client.get("/v1/guardrails", headers=_as(token)).status_code == 403


def test_only_the_operator_points_a_provider_at_a_deployment_key(gateway_client) -> None:
    # Provider keys in UMAI_DE_GATEWAY_PROVIDER_KEYS belong to the deployment (review fix, Q47).
    provider = {
        "kind": "azure_openai",
        "base_url": "https://contoso-hr.openai.azure.com/openai/v1/",
        "credential": "secret",
        "secret": "openai",
    }
    owner = _as(_assertion(roles=("owner",), sub="auth0|lin"))
    refused = gateway_client.put("/v1/providers/mine", json=provider, headers=owner)
    assert refused.status_code == 403
    assert refused.json()["detail"]["error"] == "forbidden"
    pass_through = {k: v for k, v in provider.items() if k not in ("credential", "secret")}
    assert (
        gateway_client.put("/v1/providers/mine", json=pass_through, headers=owner).status_code
        == 201
    )
    operator = {"X-Umai-Tenant": "default"}
    assert (
        gateway_client.put("/v1/providers/shared", json=provider, headers=operator).status_code
        == 201
    )
