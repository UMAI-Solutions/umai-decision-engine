"""SaaS production-readiness checks (docs/security/gap-analysis.md).

Each test is the executable form of one control. A control that does not hold yet is marked
``xfail(strict=True)`` with its gap id: the suite stays green, the gap stays measured, and
when the control is implemented the test turns XPASS and fails CI until the marker is removed
(so the gap analysis gets updated in the same change). Controls that hold today run as
ordinary tests: they are the evidence we can already show.

Synthetic data only (AGENTS.md rule 2).
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from umai_decision_engine.api.app import create_app
from umai_decision_engine.control_plane.app import create_app as create_control_app
from umai_decision_engine.settings import Settings

API_TOKEN = "test-api-token"  # synthetic
TCKN = "10000000146"  # synthetic test vector (docs/reference/turkish-identifiers.md)
PII_TEXT = f"TC kimlik {TCKN} ve IBAN TR33 0006 1005 1978 6457 8413 26"


def _request(text: str = PII_TEXT, tenant_id: str = "tenant-a", **overrides) -> dict:
    body = {
        "contract_version": "0.5",
        "request_id": "req-readiness",
        "timestamp": "2026-10-17T09:00:00Z",
        "tenant_id": tenant_id,
        "bundle": {"id": "gr-test"},
        "phase": "PRE_LLM",
        "content": {"messages": [{"role": "user", "content": text}], "focus": "LAST_USER_MESSAGE"},
        "flags": {"trace": True},
    }
    body.update(overrides)
    return body


@pytest.fixture
def client(write_bundle, detector):
    write_bundle(bundle_yaml(), active=1)
    settings = Settings(
        env="test",
        api_token=API_TOKEN,
        policy_dir=write_bundle.root,
        bundle_poll_interval_s=0,
        l0_threads=1,
    )
    with TestClient(
        create_app(settings, detector=detector), headers={"Authorization": f"Bearer {API_TOKEN}"}
    ) as c:
        yield c


# ------------------------------------------------------------ controls that hold today


def test_t2_oversized_input_is_refused_not_scanned(client, caplog) -> None:
    """Content above the size limit gets HTTP 413 and nothing is evaluated (gap T2 closed
    2026-10-19, evaluation API 0.9)."""

    huge = "Toplantı notlarını özetler misin? " * 20_000  # ~700 kB, far above any plan limit
    with caplog.at_level(logging.INFO):
        response = client.post("/v1/evaluate", json=_request(huge))
    assert response.status_code == 413
    assert response.json()["detail"]["error"] == "input_too_large"
    assert not [r for r in caplog.records if r.getMessage().startswith("decision ")]


def test_l1_validation_errors_do_not_echo_raw_content(client) -> None:
    """A malformed request is rejected without reflecting the content back (rule 1; gap L1
    closed 2026-10-19, evaluation API 0.8)."""

    bad = _request()
    del bad["timestamp"]  # a missing field: pydantic reports the whole object as `input`
    response = client.post("/v1/evaluate", json=bad)
    assert response.status_code == 422
    assert TCKN not in response.text


def test_l1_policy_service_validation_errors_do_not_echo_raw_content(
    write_bundle, detector, tmp_path
) -> None:
    """The policy service answers a malformed request like the engine API: where and how, never
    what it said, in the body or the query (launch plan S0-4, closed 2026-10-31, organizations
    0.6). A caller-chosen key is shown as ``<key>``."""

    write_bundle(bundle_yaml(), active=1)
    settings = Settings(
        env="development", policy_dir=write_bundle.root, control_db_path=tmp_path / "c.sqlite"
    )
    with TestClient(create_control_app(settings, detector=detector)) as control:
        control.post("/v1/tenants", json={"id": "tenant-a"})
        body = {"name": PII_TEXT, "scopes": TCKN, PII_TEXT: TCKN}
        refused = [
            control.post("/v1/tenants/tenant-a/keys", json=body),
            control.post(
                "/v1/tenants/tenant-a/keys",
                content=f'{{"name": "{PII_TEXT}'.encode(),
                headers={"content-type": "application/json"},
            ),
            control.get("/v1/usage", params={"month": TCKN}),
        ]
    for response in refused:
        assert response.status_code == 422
        assert TCKN not in response.text and "TR33" not in response.text
        assert response.json()["detail"]["error"] == "invalid_request"
    problems = refused[0].json()["detail"]["problems"]
    assert {"loc": ["body", "scopes"], "type": "list_type"} in problems


def test_c1_raw_content_never_reaches_logs_or_response_metadata(client, caplog) -> None:
    """ADR-0005: logs and every response field except the masked text carry no raw value."""

    caplog.set_level(logging.DEBUG)
    response = client.post("/v1/evaluate", json=_request())
    assert response.status_code == 200
    assert response.json()["decision"]["action"] == "BLOCK"
    for raw in (TCKN, "TR330006100519786457841326", "TR33 0006"):
        assert raw not in response.text  # masked text carries placeholders, never the value
        assert raw not in caplog.text


def test_c2_tenant_hashing_keys_are_distinct_per_tenant(write_bundle) -> None:
    """ADR-0005: entity hashes of two tenants are never correlatable (different HMAC keys)."""

    settings = Settings(env="development", policy_dir=write_bundle.root)
    assert settings.tenant_key("tenant-a") != settings.tenant_key("tenant-b")


def test_t1_credential_tenant_must_match_body_tenant(write_bundle, detector, tmp_path) -> None:
    """ADR-0023 §5: a key issued to tenant A must not evaluate as tenant B: HTTP 403. The engine
    resolves the tenant from the credential (signed key registry); the body field is checked
    against it. The shared operator token of ADR-0016 stays valid for self-hosted installs and
    acts as the body tenant (ADR-0023 §8); SaaS engines run without it."""

    from umai_decision_engine.control_plane.app import create_app as create_control_app

    write_bundle(bundle_yaml(), active=1)
    registry_dir = tmp_path / "keys"
    control = Settings(
        env="development",
        policy_dir=write_bundle.root,
        control_db_path=tmp_path / "c.sqlite",
        key_registry_dir=registry_dir,
    )
    with TestClient(create_control_app(control, detector=detector)) as cp:
        cp.post("/v1/tenants", json={"id": "tenant-a"})
        secret = cp.post("/v1/tenants/tenant-a/keys", json={"name": "t1"}).json()["secret"]
    engine = Settings(
        env="test",
        policy_dir=write_bundle.root,
        key_registry_dir=registry_dir,
        require_signed_bundles=False,  # no signing key in this fixture; signature tested elsewhere
        bundle_poll_interval_s=0,
        key_registry_poll_interval_s=0,
        l0_threads=1,
    )
    with TestClient(
        create_app(engine, detector=detector), headers={"Authorization": f"Bearer {secret}"}
    ) as c:
        assert c.post("/v1/evaluate", json=_request(tenant_id="tenant-a")).status_code == 200
        response = c.post("/v1/evaluate", json=_request(tenant_id="tenant-b"))
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "tenant_mismatch"


def test_t8_bad_credentials_are_throttled_per_source(client) -> None:
    """ASVS 6.1.1 / 6.3.1 (ADR-0046): after 20 bad credentials from one address in a minute,
    every credential from it gets 429 before any lookup; another address is not affected.
    Closed 2026-10-30."""

    app = client.app
    attacker = TestClient(app, client=("198.51.100.7", 50000))
    neighbour = TestClient(app, client=("203.0.113.10", 50000))
    wrong = {"Authorization": "Bearer not-the-token"}
    right = {"Authorization": f"Bearer {API_TOKEN}"}
    statuses = [
        attacker.post("/v1/evaluate", json=_request(), headers=wrong).status_code for _ in range(20)
    ]
    assert statuses == [401] * 20
    throttled = attacker.post("/v1/evaluate", json=_request(), headers=right)
    assert throttled.status_code == 429
    assert throttled.json()["detail"]["error"] == "too_many_failed_attempts"
    assert int(throttled.headers["Retry-After"]) >= 1
    assert neighbour.post("/v1/evaluate", json=_request(), headers=right).status_code == 200


def test_t9_client_address_comes_from_trusted_proxies_only(
    write_bundle, detector, tmp_path
) -> None:
    """ASVS 15.3.4 (ADR-0046): a key limited to a network is refused from elsewhere, also when
    the caller forges X-Forwarded-For; only a trusted proxy's header names the client.
    Closed 2026-10-30."""

    from umai_decision_engine.control_plane.app import create_app as create_control_app

    write_bundle(bundle_yaml(), active=1)
    registry_dir = tmp_path / "keys"
    control = Settings(
        env="development",
        policy_dir=write_bundle.root,
        control_db_path=tmp_path / "c.sqlite",
        key_registry_dir=registry_dir,
    )
    with TestClient(create_control_app(control, detector=detector)) as cp:
        cp.post("/v1/tenants", json={"id": "tenant-a"})
        body = {"name": "t9", "allowed_ips": ["203.0.113.0/24"]}
        secret = cp.post("/v1/tenants/tenant-a/keys", json=body).json()["secret"]
    engine = Settings(
        env="test",
        policy_dir=write_bundle.root,
        key_registry_dir=registry_dir,
        require_signed_bundles=False,  # no signing key in this fixture; signature tested elsewhere
        bundle_poll_interval_s=0,
        key_registry_poll_interval_s=0,
        l0_threads=1,
        trusted_proxies=["192.0.2.10/32"],
    )
    auth = {"Authorization": f"Bearer {secret}"}
    forged = {**auth, "X-Forwarded-For": "203.0.113.10"}
    with TestClient(create_app(engine, detector=detector)) as c:
        app = c.app
        direct = TestClient(app, client=("198.51.100.7", 50000))
        assert direct.post("/v1/evaluate", json=_request(), headers=forged).status_code == 403
        proxy = TestClient(app, client=("192.0.2.10", 50000))
        assert proxy.post("/v1/evaluate", json=_request(), headers=forged).status_code == 200
        spoofed = {**auth, "X-Forwarded-For": "203.0.113.10, 198.51.100.7"}
        refused = proxy.post("/v1/evaluate", json=_request(), headers=spoofed)
    assert refused.status_code == 403
    assert refused.json()["detail"]["error"] == "ip_not_allowed"


def test_a1_activation_is_attributed_to_an_actor(write_bundle, detector, tmp_path) -> None:
    """Every configuration change records the acting principal: the person of the on-behalf-of
    assertion, or the operator (control token alone, development here). Closed 2026-10-19."""

    write_bundle(bundle_yaml(), active=1)
    write_bundle(bundle_yaml(version=2), version=2)
    settings = Settings(
        env="development", policy_dir=write_bundle.root, control_db_path=tmp_path / "c.sqlite"
    )
    with TestClient(create_control_app(settings, detector=detector)) as control:
        activated = control.post("/v1/guardrails/gr-test/activate", json={"version": 2})
        assert activated.status_code == 200
        history = control.get("/v1/guardrails/gr-test").json()["history"]
    assert history and history[-1].get("actor")


def test_m1_request_quota_refuses_limit_plus_one_unevaluated(
    write_bundle, detector, tmp_path
) -> None:
    """ADR-0019 §4, ADR-0028 Validation, ADR-0043: with a monthly limit of N, request N + 1 gets
    429 ``quota_exceeded`` and nothing is evaluated: no decision, never ``BLOCK`` or ``ALLOW``.
    Closed 2026-10-30 (launch plan S1-5 in part)."""

    write_bundle(bundle_yaml(), active=1)
    registry_dir = tmp_path / "keys"
    plans = tmp_path / "plans.yaml"
    plans.write_text(
        'contract_version: "0.1"\nplans:\n  community:\n    monthly_requests: 2\n',
        encoding="utf-8",
    )
    control = Settings(
        env="development",
        policy_dir=write_bundle.root,
        control_db_path=tmp_path / "c.sqlite",
        key_registry_dir=registry_dir,
        plans_file=plans,
    )
    with TestClient(create_control_app(control, detector=detector)) as cp:
        cp.post("/v1/tenants", json={"id": "tenant-a"})
        secret = cp.post("/v1/tenants/tenant-a/keys", json={"name": "m1"}).json()["secret"]
    engine = Settings(
        env="test",
        policy_dir=write_bundle.root,
        key_registry_dir=registry_dir,
        require_signed_bundles=False,  # no signing key in this fixture; signature tested elsewhere
        bundle_poll_interval_s=0,
        key_registry_poll_interval_s=0,
        l0_threads=1,
    )
    with TestClient(
        create_app(engine, detector=detector), headers={"Authorization": f"Bearer {secret}"}
    ) as c:
        statuses = [c.post("/v1/evaluate", json=_request()).status_code for _ in range(2)]
        response = c.post("/v1/evaluate", json=_request())
    assert statuses == [200, 200]
    assert response.status_code == 429
    body = response.json()
    assert body["detail"]["error"] == "quota_exceeded"
    assert "decision" not in body and "event_id" not in body


# ------------------------------------------------------------ gaps (xfail until closed)
