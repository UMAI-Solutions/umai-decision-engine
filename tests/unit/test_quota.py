"""Plans and monthly quotas (ADR-0043): the in-process counter, plans and overrides, the 429 at
admission, entitlements in the key registry. Synthetic data only."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from umai_decision_engine.contracts.credentials import KeyRegistrySnapshot
from umai_decision_engine.contracts.entitlements import Limits, PlansFile, PlanUpdate
from umai_decision_engine.control_plane.plans import Plans
from umai_decision_engine.db.repositories import TenantInfo
from umai_decision_engine.tenancy.admission import Admission, quota_exceeded
from umai_decision_engine.tenancy.quota import (
    QuotaCounter,
    QuotaRefusal,
    month_bounds,
    quota_environment,
)
from umai_decision_engine.tenancy.registry import DirectoryKeyRegistrySource, KeyRegistry

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeLedger:
    """What other processes wrote: a count per tenant and environment."""

    def __init__(self) -> None:
        self.counts: dict[str, dict[str, int]] = {}
        self.fail = False
        self.asked: list[str] = []

    async def month(self, slug: str, start: datetime, end: datetime) -> dict[str, int]:
        self.asked.append(slug)
        if self.fail:
            raise ConnectionError("store down")
        return self.counts.get(slug, {"live": 0, "test": 0})


def test_month_bounds_reset_on_the_first_of_next_month_utc() -> None:
    assert month_bounds(None, NOW) == (
        "2026-10",
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 11, 1, tzinfo=UTC),
    )
    assert month_bounds("2026-12")[2] == datetime(2027, 1, 1, tzinfo=UTC)


def test_the_limit_is_reached_exactly_and_then_refused() -> None:
    counter = QuotaCounter(None, clock=Clock())
    assert [counter.admit("acme", "live", 3) for _ in range(3)] == [None, None, None]
    refusal = counter.admit("acme", "live", 3)
    assert refusal == QuotaRefusal(3, 3, "live", datetime(2026, 11, 1, tzinfo=UTC))
    assert counter.used("acme", "live") == 3  # a refused request is not counted


def test_no_limit_means_metered_never_refused() -> None:
    counter = QuotaCounter(None, clock=Clock())
    assert all(counter.admit("acme", "live", None) is None for _ in range(50))


def test_test_keys_have_their_own_count_and_tenants_are_apart() -> None:
    counter = QuotaCounter(None, clock=Clock())
    assert counter.admit("acme", "test", 1) is None
    assert counter.admit("acme", "test", 1) is not None
    assert counter.admit("acme", "live", 1) is None
    assert counter.admit("other", "live", 1) is None
    assert quota_environment("test") == "test"
    assert quota_environment("live") == quota_environment(None) == "live"


def test_a_new_month_starts_at_zero() -> None:
    clock = Clock()
    counter = QuotaCounter(None, clock=clock)
    counter.admit("acme", "live", 1)
    assert counter.admit("acme", "live", 1) is not None
    clock.now = datetime(2026, 11, 1, 0, 0, 1, tzinfo=UTC)
    assert counter.admit("acme", "live", 1) is None


def test_refresh_takes_the_ledger_and_keeps_what_was_admitted_meanwhile() -> None:
    ledger = FakeLedger()
    counter = QuotaCounter(ledger, clock=Clock())
    for _ in range(4):
        counter.admit("acme", "live", 100)
    # the ledger now holds these 4 plus 90 from the gateway process
    ledger.counts["acme"] = {"live": 94, "test": 2}
    assert asyncio.run(counter.refresh()) == 1
    assert counter.used("acme", "live") == 94
    assert counter.used("acme", "test") == 2
    for _ in range(6):
        assert counter.admit("acme", "live", 100) is None
    assert counter.admit("acme", "live", 100) is not None  # 94 + 6


def test_refresh_reads_only_tenants_that_asked_or_are_watched() -> None:
    ledger = FakeLedger()
    counter = QuotaCounter(ledger, clock=Clock())
    counter.admit("acme", "live", 10)
    counter.watch(["beta"])
    asyncio.run(counter.refresh())
    assert sorted(ledger.asked) == ["acme", "beta"]
    ledger.asked.clear()
    assert asyncio.run(counter.refresh()) == 0  # nobody asked since
    assert ledger.asked == []


def test_an_unreadable_ledger_keeps_the_last_count_and_admits(caplog) -> None:
    ledger = FakeLedger()
    ledger.counts["acme"] = {"live": 5, "test": 0}
    counter = QuotaCounter(ledger, clock=Clock())
    counter.watch(["acme"])
    asyncio.run(counter.refresh())
    ledger.fail = True
    counter.admit("acme", "live", 10)
    assert asyncio.run(counter.refresh()) == 0
    assert counter.used("acme", "live") == 6
    assert counter.admit("acme", "live", 10) is None
    assert "quota.refresh_failed tenant=acme error=ConnectionError" in caplog.text
    ledger.fail = False
    assert asyncio.run(counter.refresh()) == 1  # retried, not forgotten


# ------------------------------------------------------------------ plans


def _tenant(edition: str = "community", overrides: dict | None = None) -> TenantInfo:
    import uuid

    return TenantInfo(
        uuid.uuid4(), "acme", "Acme", "eu", "active", NOW, edition, entitlement_overrides=overrides
    )


def _plans() -> Plans:
    return Plans(
        PlansFile.model_validate(
            yaml.safe_load((ROOT / "deploy" / "plans.yaml").read_text(encoding="utf-8"))
        )
    )


def test_the_shipped_plans_name_the_community_limits() -> None:
    limits = _plans().limits(_tenant())
    assert limits == Limits(
        monthly_requests=10_000, monthly_test_requests=1_000, max_input_chars=32_000
    )
    assert _plans().limits(_tenant("enterprise")) == Limits()


def test_overrides_replace_single_limits_and_null_lifts_one() -> None:
    plans = _plans()
    tenant = _tenant(overrides={"monthly_requests": 50_000, "monthly_test_requests": None})
    limits = plans.limits(tenant)
    assert (limits.monthly_requests, limits.monthly_test_requests, limits.max_input_chars) == (
        50_000,
        None,
        32_000,
    )
    block = plans.entitlements(tenant)
    assert block is not None and block.edition == "community"


def test_without_a_plans_file_there_are_no_entitlements() -> None:
    assert Plans().entitlements(_tenant()) is None
    assert Plans().limits(_tenant()) == Limits()
    assert Plans.load(None).file is None


def test_a_plan_update_keeps_only_the_fields_it_names() -> None:
    update = PlanUpdate.model_validate(
        {"edition": "enterprise", "overrides": {"monthly_requests": None}}
    )
    assert update.override_values() == {"monthly_requests": None}
    assert PlanUpdate.model_validate({"edition": "community"}).override_values() == {}


# ------------------------------------------------------------------ admission and registry


def _registry(tmp_path: Path, entitlements: dict | None, tenant: str = "acme") -> KeyRegistry:
    snapshot = {
        "contract_version": "0.4",
        "tenant_id": tenant,
        "generated_at": "2026-10-30T12:00:00Z",
        "keys": [],
    }
    if entitlements is not None:
        snapshot["entitlements"] = entitlements
    KeyRegistrySnapshot.model_validate(snapshot)
    (tmp_path / f"{tenant}.json").write_text(json.dumps(snapshot), encoding="utf-8")
    registry = KeyRegistry(
        DirectoryKeyRegistrySource(tmp_path), public_keys={}, require_signed=False
    )
    registry.reload(force=True)
    return registry


def _admission(registry: KeyRegistry, quota: QuotaCounter | None = None) -> Admission:
    return Admission(
        shared_token=None,
        registry=registry,
        limiter=None,
        development=False,
        quota=quota or QuotaCounter(None, clock=Clock()),
    )


def test_the_registry_keeps_a_tenants_limits_and_its_last_good_ones(tmp_path) -> None:
    registry = _registry(tmp_path, {"edition": "community", "monthly_requests": 2})
    assert registry.entitlements("acme").monthly_requests == 2
    assert registry.limited_tenants() == ["acme"]
    (tmp_path / "acme.json").write_text("{not json", encoding="utf-8")
    registry.reload()
    assert registry.entitlements("acme").monthly_requests == 2  # last known good
    assert registry.state.errors


def test_admission_refuses_over_the_limit_with_the_contract_body(tmp_path) -> None:
    admission = _admission(_registry(tmp_path, {"edition": "community", "monthly_requests": 1}))
    admission.admit_quota("acme", "live")
    with pytest.raises(HTTPException) as refused:
        admission.admit_quota("acme", "live")
    assert refused.value.status_code == 429
    detail = refused.value.detail
    assert (detail["error"], detail["limit"], detail["used"], detail["environment"]) == (
        "quota_exceeded",
        1,
        1,
        "live",
    )
    assert int(refused.value.headers["Retry-After"]) > 0
    admission.admit_quota("acme", "test")  # no test limit: metered only
    admission.admit_quota("nobody", "live")  # no snapshot: no quota


def test_retry_after_counts_to_the_reset() -> None:
    refusal = QuotaRefusal(5, 5, "test", datetime(2026, 11, 1, tzinfo=UTC))
    exc = quota_exceeded(refusal, now=datetime(2026, 10, 31, 23, 0, tzinfo=UTC))
    assert exc.headers["Retry-After"] == "3600"
    assert exc.detail["resets_at"] == "2026-11-01T00:00:00Z"


def test_the_size_limit_is_the_tenants_capped_by_the_deployments(tmp_path) -> None:
    admission = _admission(_registry(tmp_path, {"edition": "community", "max_input_chars": 2_000}))
    assert admission.max_input_chars("acme", 32_000) == 2_000
    assert admission.max_input_chars("nobody", 32_000) == 32_000
    capped = _admission(_registry(tmp_path, {"edition": "enterprise", "max_input_chars": 64_000}))
    assert capped.max_input_chars("acme", 32_000) == 32_000
