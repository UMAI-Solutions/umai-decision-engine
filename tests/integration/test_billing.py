"""Billing end to end in the policy service against a fake iyzico (ADR-0044, contract billing
0.1): plans on offer, billing details, checkout confirmed by token, the edition and its snapshot,
signed and deduplicated webhooks, dunning (retries, grace, fallback to Community), cancel at the
period end, plan changes, roles. Synthetic persons and companies only."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from tests.integration.fake_iyzico import API_KEY, MERCHANT_ID, SECRET_KEY, FakeIyzico
from tests.integration.test_organizations import (
    ADMIN_URL,
    APP_URL,
    CONTROL,
    KEY,
    _create,
    _reset_postgres,
    person,
)
from umai_decision_engine.control_plane.actors import public_key_b64
from umai_decision_engine.control_plane.app import create_app
from umai_decision_engine.settings import Settings

ORG = "acme-security"
START = datetime(2026, 10, 30, 12, tzinfo=UTC)
PLANS = """
contract_version: "0.2"
plans:
  community:
    monthly_requests: 10000
  pro:
    monthly_requests: 100000
    billing: {price: "1499.00", currency: TRY, interval: MONTHLY}
  enterprise:
    monthly_requests: 1000000
    billing: {price: "4999.00", currency: TRY, interval: MONTHLY}
"""
ACCOUNT = {
    "company_name": "Acme Teknoloji A.Ş.",
    "tax_number": "1234567890",
    "tax_office": "Kadıköy",
    "contact_name": "Ayşe",
    "contact_surname": "Demir",
    "email": "billing@example.com",
    "phone": "+905551112233",
    "address": "Örnek Mah. Deneme Sok. No: 1",
    "city": "İstanbul",
}


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(params=["sqlite", "postgres"])
def billing(request, write_bundle, detector, tmp_path):
    database = {}
    if request.param == "postgres":  # row-level security, grants, the global link table
        if not (ADMIN_URL and APP_URL):
            pytest.skip("no test Postgres configured")
        _reset_postgres()
        database = {"database_url": APP_URL, "database_admin_url": ADMIN_URL}

    def make(*, provider: str = "iyzico", price: str = "1499.00"):
        fake = FakeIyzico(START)
        refs = {"pro": fake.add_plan(price), "enterprise": fake.add_plan("4999.00")}
        plans = tmp_path / "plans.yaml"
        plans.write_text(PLANS, encoding="utf-8")
        clock = Clock()
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
            plans_file=plans,
            billing_provider=provider,
            iyzico_api_key=API_KEY,
            iyzico_secret_key=SECRET_KEY,
            iyzico_merchant_id=MERCHANT_ID,
            billing_plan_refs=refs,
            billing_return_url="https://console.example/billing/return",
            billing_reconcile_interval_s=0,
        )
        app = create_app(
            settings,
            detector=detector,
            billing_transport=httpx.MockTransport(fake.handler),
            billing_clock=clock,
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        client.headers["Authorization"] = f"Bearer {CONTROL}"
        _create(client, "ada", org_id=ORG)
        return client, fake, clock

    clients: list[TestClient] = []
    yield make
    for client in clients:
        client.__exit__(None, None, None)


def _owner() -> dict[str, str]:
    return person("ada", tenant=ORG)  # a fresh assertion (they live two minutes)


def _snapshot_edition(client: TestClient) -> str | None:
    path = client.app.state.settings.key_registry_dir / f"{ORG}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("entitlements", {}).get("edition")


def _subscribe(client: TestClient, fake: FakeIyzico) -> str:
    assert client.put("/v1/billing/account", json=ACCOUNT, headers=_owner()).status_code == 200
    started = client.post("/v1/billing/checkout", json={"edition": "pro"}, headers=_owner())
    assert started.status_code == 200, started.text
    checkout = started.json()
    token = next(iter(fake.forms))
    sub = fake.pay(token)
    done = client.post(
        f"/v1/billing/checkouts/{checkout['checkout_id']}/complete",
        json={"token": token},
        headers={"X-Umai-Tenant": ORG},
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "active" and done.json()["edition"] == "pro"
    return sub


def _reconcile(client: TestClient) -> int:
    return client.portal.call(client.app.state.billing.reconcile)


def _billing(client: TestClient) -> dict:
    response = client.get("/v1/billing", headers=_owner())
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------ offer, details, checkout


def test_plans_on_offer_and_billing_details_come_first(billing) -> None:
    client, _, _ = billing()
    view = _billing(client)
    assert (view["provider"], view["edition"], view["can_manage"]) == ("iyzico", "community", True)
    assert [(p["edition"], p["price"], p["available"]) for p in view["plans"]] == [
        ("pro", "1499.00", True),  # cheapest first
        ("enterprise", "4999.00", True),
    ]
    refused = client.post("/v1/billing/checkout", json={"edition": "pro"}, headers=_owner())
    assert refused.status_code == 409
    assert refused.json()["detail"]["error"] == "billing_account_required"
    bad = client.put(
        "/v1/billing/account", json={**ACCOUNT, "phone": "5551112233"}, headers=_owner()
    )
    assert bad.status_code == 422


def test_checkout_is_confirmed_by_token_and_moves_the_tenant_to_pro(billing) -> None:
    client, fake, _ = billing()
    _subscribe(client, fake)
    form = next(iter(fake.forms.values()))
    assert form["callback"].startswith("https://console.example/billing/return?org=acme-security")
    assert form["customer"]["identityNumber"] == "11111111111"  # never a real TCKN
    assert form["customer"]["gsmNumber"] == "+905551112233"
    view = _billing(client)
    assert view["edition"] == "pro"
    assert view["subscription"]["status"] == "active"
    assert view["subscription"]["current_period_end"].startswith("2026-11-29")
    assert _snapshot_edition(client) == "pro"  # the engines get Pro's limits


def test_the_callback_alone_proves_nothing(billing) -> None:
    client, fake, _ = billing()
    client.put("/v1/billing/account", json=ACCOUNT, headers=_owner())
    checkout = client.post("/v1/billing/checkout", json={"edition": "pro"}, headers=_owner()).json()
    token = next(iter(fake.forms))
    # the buyer never paid: iyzico has no result for the token
    unpaid = client.post(
        f"/v1/billing/checkouts/{checkout['checkout_id']}/complete",
        json={"token": token},
        headers={"X-Umai-Tenant": ORG},
    )
    assert unpaid.status_code == 502
    wrong = client.post(
        f"/v1/billing/checkouts/{checkout['checkout_id']}/complete",
        json={"token": "tok-forged"},
        headers={"X-Umai-Tenant": ORG},
    )
    assert wrong.status_code == 409
    assert _billing(client)["edition"] == "community"
    fake.pay(token)
    first = client.post(
        f"/v1/billing/checkouts/{checkout['checkout_id']}/complete",
        json={"token": token},
        headers={"X-Umai-Tenant": ORG},
    )
    again = client.post(
        f"/v1/billing/checkouts/{checkout['checkout_id']}/complete",
        json={"token": token},
        headers={"X-Umai-Tenant": ORG},
    )
    assert first.json() == again.json() and first.json()["status"] == "active"
    assert (
        client.post("/v1/billing/checkout", json={"edition": "pro"}, headers=_owner()).status_code
        == 409
    )


def test_a_price_that_differs_from_the_provider_plan_is_not_sold(billing) -> None:
    client, _, _ = billing(price="999.00")
    offers = {p["edition"]: p["available"] for p in _billing(client)["plans"]}
    assert offers == {"enterprise": True, "pro": False}
    client.put("/v1/billing/account", json=ACCOUNT, headers=_owner())
    refused = client.post("/v1/billing/checkout", json={"edition": "pro"}, headers=_owner())
    assert refused.status_code == 422
    assert refused.json()["detail"]["error"] == "plan_not_available"


def test_without_a_provider_nothing_is_sold(billing) -> None:
    client, _, _ = billing(provider="none")
    view = _billing(client)
    assert view["provider"] == "none"
    assert all(not p["available"] for p in view["plans"])


def test_buying_needs_a_person(billing) -> None:
    client, _, _ = billing()
    refused = client.post(
        "/v1/billing/checkout", json={"edition": "pro"}, headers={"X-Umai-Tenant": ORG}
    )
    assert refused.status_code == 403
    assert refused.json()["detail"]["error"] == "actor_required"
    stranger = client.get("/v1/billing", headers=person("eve", tenant=ORG))
    assert stranger.status_code == 403


# ------------------------------------------------------------------ webhooks and dunning


def test_webhooks_are_signed_deduplicated_and_only_trigger_a_read(billing) -> None:
    client, fake, _ = billing()
    sub = _subscribe(client, fake)
    order = fake.renew(sub, success=False)
    payload, signature = fake.webhook(sub, order, success=False)
    forged = client.post("/hooks/iyzico", json=payload, headers={"X-IYZ-SIGNATURE-V3": "0" * 64})
    assert forged.status_code == 401
    unsigned = client.post("/hooks/iyzico", json=payload)
    assert unsigned.status_code == 401
    for _ in range(2):  # iyzico resends: applied once
        ok = client.post("/hooks/iyzico", json=payload, headers={"X-IYZ-SIGNATURE-V3": signature})
        assert ok.status_code == 200 and ok.json() == {"received": True}
    view = _billing(client)["subscription"]
    assert view["status"] == "past_due"
    assert view["last_payment_error"] == "Yetersiz bakiye"
    assert view["grace_until"].startswith("2026-11-13")
    assert view["next_retry_at"].startswith("2026-10-31")
    assert _billing(client)["edition"] == "pro"  # grace: the paid limits stay
    unknown, sig = fake.webhook(sub, order, success=True)
    unknown["subscriptionReferenceCode"] = "sub-nobody"
    from umai_decision_engine.billing.iyzico import webhook_signature

    sig = webhook_signature(MERCHANT_ID, SECRET_KEY, unknown)
    assert (
        client.post("/hooks/iyzico", json=unknown, headers={"X-IYZ-SIGNATURE-V3": sig}).status_code
        == 200
    )


def test_retries_follow_the_schedule_and_a_paid_retry_ends_past_due(billing) -> None:
    client, fake, clock = billing()
    sub = _subscribe(client, fake)
    order = fake.renew(sub, success=False)
    _reconcile(client)  # a missed webhook: the reconciliation finds the failure
    assert _billing(client)["subscription"]["status"] == "past_due"
    assert fake.retries == []
    clock.now += timedelta(days=1, minutes=1)
    _reconcile(client)
    assert fake.retries == [order]
    assert _billing(client)["subscription"]["next_retry_at"].startswith("2026-11-02")
    fake.settle_retry(sub, order)
    payload, signature = fake.webhook(sub, order, success=True)
    client.post("/hooks/iyzico", json=payload, headers={"X-IYZ-SIGNATURE-V3": signature})
    view = _billing(client)
    assert view["subscription"]["status"] == "active"
    assert view["subscription"]["grace_until"] is None
    assert view["edition"] == "pro"


def test_an_unpaid_grace_period_falls_back_to_community(billing) -> None:
    client, fake, clock = billing()
    sub = _subscribe(client, fake)
    fake.renew(sub, success=False)
    _reconcile(client)
    clock.now += timedelta(days=15)
    _reconcile(client)
    view = _billing(client)
    assert view["subscription"]["status"] == "ended"
    assert view["edition"] == "community"
    assert _snapshot_edition(client) == "community"
    assert fake.subscriptions[sub]["subscriptionStatus"] == "CANCELED"
    # a new checkout is possible again
    client.post(
        "/v1/billing/checkout", json={"edition": "pro"}, headers=_owner()
    ).raise_for_status()


# ------------------------------------------------------------------ cancel and plan changes


def test_a_cancellation_keeps_the_paid_edition_until_the_period_ends(billing) -> None:
    client, fake, clock = billing()
    _subscribe(client, fake)
    canceled = client.post("/v1/billing/cancel", headers=_owner())
    assert canceled.status_code == 200, canceled.text
    sub = canceled.json()["subscription"]
    assert sub["status"] == "canceled" and sub["cancel_at"].startswith("2026-11-29")
    assert canceled.json()["edition"] == "pro"
    clock.now += timedelta(days=31)
    _reconcile(client)
    view = _billing(client)
    assert (view["subscription"]["status"], view["edition"]) == ("ended", "community")


def test_billing_changes_are_audited_without_billing_details(billing) -> None:
    client, fake, _ = billing()
    _subscribe(client, fake)
    assert client.post("/v1/billing/cancel", headers=_owner()).status_code == 200
    listed = client.get("/v1/audit-events", params={"action": "billing"}, headers=_owner())
    events = listed.json()["events"]
    assert [e["action"] for e in reversed(events)] == [
        "billing.account.update",
        "billing.checkout.start",
        "billing.checkout.complete",
        "billing.cancel",
    ]
    complete = events[1]
    assert complete["actor"] == {"type": "operator"}  # the console's public return route
    assert complete["details"] == {"status": "active", "edition": "pro"}
    assert events[0]["target"] == {"type": "subscription"}
    stored = json.dumps(events, ensure_ascii=False)
    assert not [v for v in ACCOUNT.values() if v in stored]  # billing details stay in billing
    assert next(iter(fake.forms)) not in stored  # nor the checkout token


def test_upgrades_apply_now_and_downgrades_from_the_next_paid_period(billing) -> None:
    client, fake, _ = billing()
    _subscribe(client, fake)
    up = client.post("/v1/billing/plan", json={"edition": "enterprise"}, headers=_owner())
    assert up.status_code == 200, up.text
    assert up.json()["edition"] == "enterprise"
    assert fake.last_upgrade["upgradePeriod"] == "NOW"
    assert _snapshot_edition(client) == "enterprise"
    down = client.post("/v1/billing/plan", json={"edition": "pro"}, headers=_owner())
    assert fake.last_upgrade["upgradePeriod"] == "NEXT_PERIOD"
    assert down.json()["edition"] == "enterprise"  # paid until the period ends
    current = next(
        ref
        for ref, s in fake.subscriptions.items()
        if s["subscriptionStatus"] == "ACTIVE"
        and s["pricingPlanReferenceCode"] == client.app.state.billing.plan_refs["pro"]
    )
    fake.renew(current, success=True)
    _reconcile(client)
    assert _billing(client)["edition"] == "pro"
