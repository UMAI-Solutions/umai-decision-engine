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


# ------------------------------------------------------------ gaps (xfail until closed)


@pytest.mark.xfail(
    strict=True,
    reason="GAP-T2: no prompt-size limit; L0 scans any length (ADR-0019 §4)",
)
def test_t2_oversized_input_is_refused_not_scanned(client) -> None:
    """Content above the tenant's size limit gets HTTP 413 and nothing is evaluated."""

    huge = "Toplantı notlarını özetler misin? " * 20_000  # ~700 kB, far above any plan limit
    response = client.post("/v1/evaluate", json=_request(huge))
    assert response.status_code == 413


@pytest.mark.xfail(
    strict=True,
    reason="GAP-L1: FastAPI 422 bodies echo the offending input, which may be raw content",
)
def test_l1_validation_errors_do_not_echo_raw_content(client) -> None:
    """A malformed request is rejected without reflecting the content back (rule 1)."""

    bad = _request()
    del bad["timestamp"]  # a missing field: pydantic reports the whole object as `input`
    response = client.post("/v1/evaluate", json=bad)
    assert response.status_code == 422
    assert TCKN not in response.text


@pytest.mark.xfail(
    strict=True,
    reason="GAP-A1: the change log has no actor; audit events need who did it (ADR-0030)",
)
def test_a1_activation_is_attributed_to_an_actor(write_bundle, detector, tmp_path) -> None:
    """Every configuration change records the acting principal."""

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
