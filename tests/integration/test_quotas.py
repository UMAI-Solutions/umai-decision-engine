"""Plans and monthly quotas end to end (ADR-0043): the policy service writes a tenant's limits
into its signed snapshot, the engine enforces them at admission and counts from the usage
ledger, `GET /v1/entitlements` reads the same count. One SQLite store shared by both, as on the
single VM. Synthetic data only."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from tests.integration.test_api_keys import CONTROL_TOKEN, _keypair, _mint, _request
from umai_decision_engine.api.app import create_app as create_engine_app
from umai_decision_engine.control_plane.app import create_app as create_control_app
from umai_decision_engine.db.session import make_engine, sqlite_url
from umai_decision_engine.events.ledger import LedgerCounts
from umai_decision_engine.settings import Settings
from umai_decision_engine.tenancy.quota import QuotaCounter, month_bounds

PLANS = """
contract_version: "0.1"
plans:
  community:
    monthly_requests: 3
    monthly_test_requests: 1
    max_input_chars: 2000
  enterprise:
    monthly_requests: null
"""


@pytest.fixture
def quota_stack(write_bundle, detector, tmp_path):
    """Policy service with a plans file and a signing key, and a factory for engines that read
    its snapshots and write records to the same store."""

    private, public = _keypair()
    write_bundle(bundle_yaml(), active=1)
    key_file = tmp_path / "signing.key"
    key_file.write_text(private, encoding="utf-8")
    plans = tmp_path / "plans.yaml"
    plans.write_text(PLANS, encoding="utf-8")
    registry_dir = tmp_path / "keys"
    database = sqlite_url(tmp_path / "store.sqlite")
    control_settings = Settings(
        env="test",
        policy_dir=write_bundle.root,
        database_url=database,
        control_api_token=CONTROL_TOKEN,
        signing_key_file=key_file,
        signing_key_id="k1",
        key_registry_dir=registry_dir,
        region="eu",
        bundle_public_keys={"k1": public},
        plans_file=plans,
        records_retention_interval_s=0,
    )
    engine_settings = Settings(
        env="test",
        policy_dir=write_bundle.root,
        bundle_public_keys={"k1": public},
        key_registry_dir=registry_dir,
        key_registry_poll_interval_s=0,
        bundle_poll_interval_s=0,
        rate_limit_rps=None,
        l0_threads=1,
        records_sink="database",
        database_url=database,
        records_flush_interval_ms=10,
    )
    with TestClient(
        create_control_app(control_settings, detector=detector),
        headers={"Authorization": f"Bearer {CONTROL_TOKEN}"},
    ) as control:
        assert control.post("/v1/tenants", json={"id": "acme"}).status_code == 201

        def engine() -> TestClient:
            return TestClient(create_engine_app(engine_settings, detector=detector))

        yield control, engine, database


def _evaluate(client: TestClient, secret: str, text: str = "Toplantı notlarını özetle") -> dict:
    response = client.post(
        "/v1/evaluate",
        json=_request("acme", text=text),
        headers={"Authorization": f"Bearer {secret}"},
    )
    return {"status": response.status_code, "body": response.json(), "headers": response.headers}


def _refresh_until(client: TestClient, live_used: int) -> None:
    """Run the engine's ledger refresh (on its own event loop) until the records it wrote are
    in the ledger."""

    quota = client.app.state.admission.quota
    for _ in range(200):
        quota.watch(["acme"])
        client.portal.call(quota.refresh)
        if quota.used("acme", "live") >= live_used:
            return
        time.sleep(0.02)
    raise AssertionError(f"ledger never reached {live_used}: {quota.used('acme', 'live')}")


def test_the_plan_reaches_the_snapshot_and_the_quota_refuses_at_the_limit(quota_stack) -> None:
    control, engine, database = quota_stack
    live, _ = _mint(control, "acme")
    test, _ = _mint(control, "acme", env="test")
    snapshot = json.loads(
        (control.app.state.settings.key_registry_dir / "acme.json").read_text(encoding="utf-8")
    )
    assert snapshot["contract_version"] == "0.4" and snapshot["signature"]
    assert snapshot["entitlements"] == {
        "edition": "community",
        "monthly_requests": 3,
        "monthly_test_requests": 1,
        "max_input_chars": 2000,
    }

    with engine() as client:
        # refused before the quota: never counted (413 above the tenant's 2 000 characters)
        too_large = _evaluate(client, live, text="a" * 2_001)
        assert too_large["status"] == 413 and too_large["body"]["detail"]["limit"] == 2000
        assert [_evaluate(client, live)["status"] for _ in range(3)] == [200, 200, 200]
        refused = _evaluate(client, live)
        assert refused["status"] == 429
        detail = refused["body"]["detail"]
        assert (detail["error"], detail["limit"], detail["used"], detail["environment"]) == (
            "quota_exceeded",
            3,
            3,
            "live",
        )
        assert detail["resets_at"] == month_bounds(None)[2].isoformat().replace("+00:00", "Z")
        assert "decision" not in refused["body"]  # nothing was evaluated
        assert int(refused["headers"]["retry-after"]) > 0
        # test keys are counted apart
        assert _evaluate(client, test)["status"] == 200
        assert _evaluate(client, test)["body"]["detail"]["environment"] == "test"
    # the engine has shut down: its records are in the ledger

    used = control.get("/v1/entitlements", headers={"X-Umai-Tenant": "acme"})
    assert used.status_code == 200, used.text
    view = used.json()
    assert (view["edition"], view["used"], view["limits"]["monthly_requests"]) == (
        "community",
        {"requests": 3, "test_requests": 1},
        3,
    )

    # a restarted engine reads the used quota from the ledger before its first request
    with engine() as client:
        assert _evaluate(client, live)["status"] == 429

    # another process (here: a fresh counter) sees the same count
    async def ledger_count() -> int:
        db = make_engine(database)
        counter = QuotaCounter(LedgerCounts(db))
        counter.watch(["acme"])
        await counter.refresh()
        await db.dispose()
        return counter.used("acme", "live")

    assert asyncio.run(ledger_count()) == 3


def test_the_operator_changes_the_plan_and_the_engine_follows(quota_stack) -> None:
    control, engine, _ = quota_stack
    live, _ = _mint(control, "acme")
    with engine() as client:
        assert [_evaluate(client, live)["status"] for _ in range(4)] == [200, 200, 200, 429]

        upgraded = control.put("/v1/tenants/acme/plan", json={"edition": "enterprise"})
        assert upgraded.status_code == 200, upgraded.text
        assert upgraded.json()["published"] is True
        assert upgraded.json()["limits"]["monthly_requests"] is None
        client.app.state.registry.reload()
        assert _evaluate(client, live)["status"] == 200  # metered, not refused

        override = control.put(
            "/v1/tenants/acme/plan",
            json={"edition": "community", "overrides": {"monthly_requests": 5}},
        )
        assert override.json()["overrides"] == {"monthly_requests": 5}
        assert override.json()["limits"]["max_input_chars"] == 2000  # the plan's, kept
        client.app.state.registry.reload()
        # the request admitted without a limit is in the ledger: 4 of 5 after a refresh
        _refresh_until(client, 4)
        assert [_evaluate(client, live)["status"] for _ in range(2)] == [200, 429]

    assert control.get("/v1/tenants/acme").json()["edition"] == "community"
    assert control.put("/v1/tenants/nobody/plan", json={"edition": "community"}).status_code == 404


def test_without_a_plans_file_nothing_is_limited(write_bundle, detector, tmp_path) -> None:
    write_bundle(bundle_yaml(), active=1)
    registry_dir = tmp_path / "keys"
    control_settings = Settings(
        env="development",
        policy_dir=write_bundle.root,
        control_db_path=tmp_path / "c.sqlite",
        key_registry_dir=registry_dir,
    )
    with TestClient(create_control_app(control_settings, detector=detector)) as control:
        control.post("/v1/tenants", json={"id": "acme"})
        _mint(control, "acme")
        view = control.get("/v1/entitlements", headers={"X-Umai-Tenant": "acme"}).json()
    snapshot = json.loads((registry_dir / "acme.json").read_text(encoding="utf-8"))
    assert "entitlements" not in snapshot
    assert view["limits"] == {
        "monthly_requests": None,
        "monthly_test_requests": None,
        "max_input_chars": None,
    }
