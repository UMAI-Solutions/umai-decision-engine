"""Webhooks end to end in the policy service (ADR-0048, contract webhooks 0.1) with a fake
receiver, a fake DNS and a clock the test moves: endpoints and roles, the secret shown once and
verified with the documented code, blocked decisions aggregated per window and sent once, quota
thresholds once per month, billing events, retries on the schedule, turning off (failing, gone),
SSRF refusals at registration and at delivery, Slack's format, and no content in any payload.
Synthetic organizations, persons and records only."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

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
from tests.unit.test_webhooks import documented_verify
from umai_decision_engine.contracts.webhooks import DATA_MODELS, WebhookEvent
from umai_decision_engine.control_plane.actors import public_key_b64
from umai_decision_engine.control_plane.app import create_app
from umai_decision_engine.db.models import (
    BillingEventRow,
    DecisionEventRow,
    SubscriptionRow,
    UsageEventRow,
)
from umai_decision_engine.db.session import tenant_session
from umai_decision_engine.settings import Settings

ORG = "acme-security"
MASTER = bytes(range(32))
START = datetime(2026, 10, 30, 12, 0, 30, tzinfo=UTC)
HOOK = "https://hooks.example.com/umai/intake"
SLACK = "https://hooks.slack.com/services/T0SYNTH/B0SYNTH/synthetic0secret0path"
MARKER = "synthetic-content-marker"  # placed where content could hide; must never be sent
PLANS = """
contract_version: "0.2"
plans:
  community:
    monthly_requests: 10
"""


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


class Receiver:
    """Answers with the queued statuses, then ``default``; keeps every request it got."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.statuses: list[int] = []
        self.default = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status = self.statuses.pop(0) if self.statuses else self.default
        return httpx.Response(status, text="receiver body, never read")

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture(params=["sqlite", "postgres"])
def webhooks(request, write_bundle, detector, tmp_path):
    database = {}
    if request.param == "postgres":  # row-level security and the append-only delivery log
        if not (ADMIN_URL and APP_URL):
            pytest.skip("no test Postgres configured")
        _reset_postgres()
        database = {"database_url": APP_URL, "database_admin_url": ADMIN_URL}
    clients: list[TestClient] = []

    def make(*, guarded: bool = False, master: bytes | None = MASTER, **overrides):
        receiver, clock = Receiver(), Clock()
        dns = {"hooks.example.com": ["93.184.216.34"], "hooks.slack.com": ["52.4.0.1"]}

        async def resolve(host: str, port: int) -> list[str]:
            if host not in dns:
                raise OSError("NXDOMAIN")
            return dns[host]

        plans = tmp_path / "plans.yaml"
        plans.write_text(PLANS, encoding="utf-8")
        write_bundle(bundle_yaml(), active=1)
        settings = Settings(
            **database,
            env="test",
            require_signed_bundles=False,
            policy_dir=write_bundle.root,
            control_db_path=tmp_path / f"control-{len(clients)}.sqlite",
            control_api_token=CONTROL,
            actor_assertion="required",
            actor_public_keys={"console-1": public_key_b64(KEY)},
            starter_guardrail="gr-test",
            key_registry_dir=tmp_path / "keys",
            plans_file=plans,
            webhook_master_key=base64.b64encode(master).decode() if master else None,
            webhooks_interval_s=0,  # the test drives the worker
            console_url="https://console.example.com/",
            records_retention_interval_s=0,
            **overrides,
        )
        app = create_app(
            settings,
            detector=detector,
            webhook_transport=None if guarded else httpx.MockTransport(receiver.handler),
            webhook_resolver=resolve,
            webhook_clock=clock,
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        client.headers["Authorization"] = f"Bearer {CONTROL}"
        if not clients[:-1]:  # the organization lives in the database, made once
            _create(client, "ada", org_id=ORG)
        service = client.app.state.webhooks
        service.config = type(service.config)(**{**service.config.__dict__, "slack_spacing_s": 0})
        return client, receiver, clock, dns

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def _owner() -> dict[str, str]:
    return person("ada", tenant=ORG)


def _add(client: TestClient, url: str = HOOK, types=("decision.blocked",), kind="generic"):
    response = client.post(
        "/v1/webhooks/endpoints",
        json={"url": url, "kind": kind, "event_types": list(types), "description": "SIEM"},
        headers=_owner(),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _tenant_id(client: TestClient) -> uuid.UUID:
    return client.portal.call(client.app.state.tenants.get, ORG).id


def _insert(client: TestClient, model, rows: list[dict]) -> None:
    tenant = _tenant_id(client)

    async def write() -> None:
        async with tenant_session(client.app.state.db, tenant) as session:
            for row in rows:
                await session.execute(insert(model).values(tenant_id=tenant, **row))

    client.portal.call(write)


def _decision(at: datetime, action: str = "BLOCK", application: str | None = "support-bot"):
    return {
        "id": uuid.uuid4(),
        "event_id": f"ev_{uuid.uuid4().hex}",
        "occurred_at": at,
        "source": "gateway" if application else "api",
        "request_id": f"req-{MARKER}",
        "engine_version": "0.1.0",
        "application_id": application,
        "bundle_id": "gr-test",
        "phase": "PRE_LLM",
        "action": action,
        "severity": "HIGH",
        "decided_by": "L0",
        "degraded": False,
        "review": False,
        "review_reasons": [],
        "subject": {"user_id": MARKER},
        "destination": {"name": MARKER},
        "entities": [{"type": "TR_TCKN", "count": 1, "value_hash": MARKER}],
        "tags": ["data.pii.national_id"],
        "matched": [{"kind": "l0_policy", "id": "tr-national-id", "action": "BLOCK"}],
        "errors": [],
        "latency_total_ms": 3.0,
    }


def _run(client: TestClient) -> tuple[int, int]:
    """One worker round: read the records, then deliver what is due."""

    service = client.app.state.webhooks
    return client.portal.call(service.collect), client.portal.call(service.deliver_due)


def _deliveries(client: TestClient, **query) -> list[dict]:
    response = client.get("/v1/webhooks/deliveries", params=query, headers=_owner())
    assert response.status_code == 200, response.text
    return response.json()["deliveries"]


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


# ------------------------------------------------------------------ endpoints


def test_create_shows_the_secret_once_and_masks_the_url(webhooks) -> None:
    client, _, _, _ = webhooks()
    created = _add(client)
    secret = created["secret"]
    endpoint = created["endpoint"]
    assert secret.startswith("whsec_")
    assert endpoint["url"] == "https://hooks.example.com/…take" and endpoint["signed"] is True
    listed = client.get("/v1/webhooks", headers=_owner())
    assert listed.status_code == 200
    body = listed.text
    assert secret not in body and "/umai/intake" not in body
    view = listed.json()
    assert view["available"] is True and view["max_endpoints"] == 10
    assert [e["endpoint_id"] for e in view["endpoints"]] == [endpoint["endpoint_id"]]
    # the stored row holds neither the secret nor the URL in clear
    rotated = client.post(
        f"/v1/webhooks/endpoints/{endpoint['endpoint_id']}/secret", headers=_owner()
    )
    assert rotated.status_code == 200 and rotated.json()["secret"] not in (None, secret)


def test_registration_refuses_unreachable_and_private_targets(webhooks) -> None:
    client, _, _, dns = webhooks()
    dns["internal.example.com"] = ["10.0.0.5"]
    for url, kind in (
        ("http://hooks.example.com/in", "generic"),
        ("https://169.254.169.254/latest", "generic"),
        ("https://internal.example.com/hook", "generic"),  # resolves into the VNet
        ("https://hooks.example.com/services/x", "slack"),
    ):
        response = client.post(
            "/v1/webhooks/endpoints",
            json={"url": url, "kind": kind, "event_types": ["quota.threshold"]},
            headers=_owner(),
        )
        assert response.status_code == 422, url
        assert response.json()["detail"]["error"] == "invalid_url"
    # a name that does not resolve yet is accepted: every attempt checks again
    _add(client, "https://not-yet.example.com/hook")


def test_roles_and_limits(webhooks) -> None:
    client, _, _, _ = webhooks(webhooks_max_endpoints=1)
    for who, roles in (("grace", ["viewer"]), ("linus", ["admin"])):
        invited = client.post(
            f"/v1/organizations/{ORG}/invitations",
            json={"email": f"{who}@example.com", "roles": roles},
            headers=_owner(),
        )
        token = invited.json()["token"]
        accepted = client.post(
            "/v1/invitations/accept",
            json={"organization": ORG, "token": token},
            headers=person(who),
        )
        assert accepted.status_code == 200, accepted.text
    viewer = person("grace", tenant=ORG)
    assert client.get("/v1/webhooks", headers=viewer).status_code == 403
    admin = person("linus", tenant=ORG)
    made = client.post(
        "/v1/webhooks/endpoints",
        json={"url": HOOK, "event_types": ["quota.threshold"]},
        headers=admin,
    )
    assert made.status_code == 201
    assert made.json()["endpoint"]["created_by"] == "person:auth0|linus"
    again = client.post(
        "/v1/webhooks/endpoints",
        json={"url": HOOK, "event_types": ["quota.threshold"]},
        headers=admin,
    )
    assert again.status_code == 409 and again.json()["detail"]["error"] == "too_many_endpoints"
    # the control token alone may read, not manage (a person is named)
    assert client.get("/v1/webhooks", headers={"X-Umai-Tenant": ORG}).status_code == 200
    operator = client.post(
        "/v1/webhooks/endpoints",
        json={"url": HOOK, "event_types": ["quota.threshold"]},
        headers={"X-Umai-Tenant": ORG},
    )
    assert operator.status_code == 403


def test_without_a_master_key_nothing_is_available(webhooks) -> None:
    client, _, _, _ = webhooks(master=None)
    assert client.get("/v1/webhooks", headers=_owner()).json()["available"] is False
    response = client.post(
        "/v1/webhooks/endpoints",
        json={"url": HOOK, "event_types": ["quota.threshold"]},
        headers=_owner(),
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "webhooks_unavailable"


# ------------------------------------------------------------------ test event and signature


def test_test_event_is_signed_and_verifies_with_the_documented_code(webhooks) -> None:
    client, receiver, clock, _ = webhooks()
    created = _add(client)
    endpoint_id = created["endpoint"]["endpoint_id"]
    clock.now = datetime.now(UTC)  # the receiver checks the timestamp against its own clock
    response = client.post(f"/v1/webhooks/endpoints/{endpoint_id}/test", headers=_owner())
    assert response.status_code == 200, response.text
    delivery = response.json()["delivery"]
    assert delivery["outcome"] == "delivered" and delivery["test"] is True
    (request,) = receiver.requests
    assert str(request.url) == HOOK
    assert documented_verify()(
        created["secret"], request.content, request.headers["x-umai-signature"]
    )
    event = WebhookEvent.model_validate_json(request.content)
    assert event.type == "webhook.test" and event.organization_id == ORG
    assert request.headers["x-umai-event-id"] == event.event_id
    assert event.links.console == "https://console.example.com/webhooks"
    assert [d["event_id"] for d in _deliveries(client)] == [event.event_id]
    # a failed test is reported, not retried, and leaves the streak alone
    receiver.default = 500
    failed = client.post(f"/v1/webhooks/endpoints/{endpoint_id}/test", headers=_owner()).json()
    assert (
        failed["delivery"]["outcome"] == "failed" and failed["delivery"]["next_attempt_at"] is None
    )
    endpoint = client.get(f"/v1/webhooks/endpoints/{endpoint_id}", headers=_owner()).json()
    assert endpoint["endpoint"]["consecutive_failures"] == 0


# ------------------------------------------------------------------ decision.blocked


def test_a_burst_of_blocks_is_one_event_per_window_without_content(webhooks) -> None:
    client, receiver, clock, _ = webhooks()
    created = _add(client)
    window = datetime(2026, 10, 30, 12, 5, tzinfo=UTC)
    burst = [_decision(window + timedelta(seconds=i)) for i in range(40)]
    burst += [_decision(window + timedelta(seconds=50), application=None)]  # an API call
    burst += [_decision(window + timedelta(seconds=55), action="ALLOW")]  # not blocked
    _insert(client, DecisionEventRow, burst)
    clock.now = window + timedelta(minutes=5, seconds=30)  # the window ended, not settled yet
    assert _run(client) == (0, 0)
    clock.advance(seconds=31)
    assert _run(client) == (1, 1)
    (request,) = receiver.requests
    raw = request.content.decode()
    assert MARKER not in raw  # neither subject, destination, request id nor value hashes
    assert not _keys(json.loads(raw)) & {"value_hash", "subject", "destination", "text", "spans"}
    event = WebhookEvent.model_validate_json(request.content)
    data = DATA_MODELS[event.type].model_validate(event.data)
    assert data.blocked == 41 and data.by_source == {"gateway": 40, "api": 1}
    assert [(a.application_id, a.blocked) for a in data.applications] == [
        ("support-bot", 40),
        (None, 1),
    ]
    assert [(e.type, e.decisions) for e in data.top_entities] == [("TR_TCKN", 41)]
    assert data.top_policies[0].id == "tr-national-id" and len(data.sample_event_ids) == 5
    assert data.window.from_ == window and event.occurred_at == window + timedelta(minutes=5)
    signature = request.headers["x-umai-signature"]
    assert signature.startswith(f"t={int(clock.now.timestamp())},v1=")  # the worker's clock
    assert documented_verify()(created["secret"], request.content, signature, tolerance_s=10**10)
    # read again (and after a restart: the position is in memory): nothing is sent twice
    client.app.state.webhooks._cursors.clear()
    assert _run(client) == (0, 0)


def test_an_endpoint_gets_only_what_happened_while_it_was_enabled(webhooks) -> None:
    client, receiver, clock, _ = webhooks()
    window = datetime(2026, 10, 30, 11, 50, tzinfo=UTC)
    _insert(client, DecisionEventRow, [_decision(window + timedelta(seconds=10))])
    clock.now = datetime(2026, 10, 30, 12, 0, 30, tzinfo=UTC)
    _add(client)  # created after that window ended
    assert _run(client) == (0, 0)
    assert receiver.requests == []


# ------------------------------------------------------------------ retries and turning off


def test_retries_follow_the_schedule_until_delivered(webhooks) -> None:
    client, receiver, clock, _ = webhooks()
    _add(client)
    window = datetime(2026, 10, 30, 12, 5, tzinfo=UTC)
    _insert(client, DecisionEventRow, [_decision(window)])
    clock.now = window + timedelta(minutes=6, seconds=1)
    receiver.statuses = [503, 502]
    assert _run(client) == (1, 1)
    first = _deliveries(client)[0]
    assert (first["outcome"], first["status_code"], first["error"]) == (
        "failed",
        503,
        "http_status",
    )
    assert first["next_attempt_at"] == (clock.now + timedelta(minutes=1)).isoformat().replace(
        "+00:00", "Z"
    )
    clock.advance(seconds=59)
    assert client.portal.call(client.app.state.webhooks.deliver_due) == 0  # not yet
    clock.advance(seconds=1)
    assert client.portal.call(client.app.state.webhooks.deliver_due) == 1
    clock.advance(minutes=5)
    assert client.portal.call(client.app.state.webhooks.deliver_due) == 1
    log = _deliveries(client)
    assert [(d["attempt"], d["outcome"]) for d in log] == [
        (3, "delivered"),
        (2, "failed"),
        (1, "failed"),
    ]
    assert len({r.headers["x-umai-event-id"] for r in receiver.requests}) == 1  # same event
    assert [r.headers["x-umai-delivery-attempt"] for r in receiver.requests] == ["1", "2", "3"]
    clock.advance(hours=7)
    assert client.portal.call(client.app.state.webhooks.deliver_due) == 0


def test_an_endpoint_failing_for_the_configured_time_turns_off(webhooks) -> None:
    client, receiver, clock, _ = webhooks(
        webhooks_disable_after_hours=1, webhooks_disable_min_failures=3
    )
    endpoint_id = _add(client)["endpoint"]["endpoint_id"]
    receiver.default = 500
    window = datetime(2026, 10, 30, 12, 5, tzinfo=UTC)
    _insert(client, DecisionEventRow, [_decision(window)])
    clock.now = window + timedelta(minutes=6, seconds=1)
    _run(client)  # attempt 1
    for minutes in (1, 5, 30):  # attempts 2-4 at +1 m, +6 m, +36 m: under an hour
        clock.advance(minutes=minutes)
        client.portal.call(client.app.state.webhooks.deliver_due)
    endpoint = client.get(f"/v1/webhooks/endpoints/{endpoint_id}", headers=_owner()).json()
    assert endpoint["endpoint"]["enabled"] is True
    assert endpoint["endpoint"]["consecutive_failures"] == 4
    clock.advance(hours=2)  # attempt 5 at +2 h 36 m: failing for over an hour
    client.portal.call(client.app.state.webhooks.deliver_due)
    endpoint = client.get(f"/v1/webhooks/endpoints/{endpoint_id}", headers=_owner()).json()
    assert endpoint["endpoint"]["enabled"] is False
    assert endpoint["endpoint"]["disabled_reason"] == "failing"
    assert _deliveries(client)[0]["next_attempt_at"] is None  # the retry was cancelled
    clock.advance(hours=7)
    assert client.portal.call(client.app.state.webhooks.deliver_due) == 0
    # turned on again: a clean slate
    on = client.patch(
        f"/v1/webhooks/endpoints/{endpoint_id}", json={"enabled": True}, headers=_owner()
    ).json()["endpoint"]
    assert on["enabled"] and on["disabled_reason"] is None and on["consecutive_failures"] == 0


def test_slack_gone_turns_the_endpoint_off_at_once_and_uses_slacks_format(webhooks) -> None:
    client, receiver, clock, _ = webhooks()
    created = _add(client, SLACK, kind="slack", types=("decision.blocked", "quota.threshold"))
    assert created["secret"] is None and created["endpoint"]["signed"] is False
    assert created["endpoint"]["url"] == "https://hooks.slack.com/…path"
    endpoint_id = created["endpoint"]["endpoint_id"]
    rotated = client.post(f"/v1/webhooks/endpoints/{endpoint_id}/secret", headers=_owner())
    assert rotated.status_code == 409
    window = datetime(2026, 10, 30, 12, 5, tzinfo=UTC)
    _insert(client, DecisionEventRow, [_decision(window)])
    _insert(  # 9 of 10 requests: 80 % reached
        client,
        UsageEventRow,
        [
            {
                "event_id": f"ev_{i}",
                "occurred_at": window,
                "meter": "evaluations",
                "quantity": 1,
                "source": "api",
                "degraded": False,
            }
            for i in range(9)
        ],
    )
    clock.now = window + timedelta(minutes=6, seconds=1)
    receiver.statuses = [200, 404]  # the second message finds the channel gone
    assert _run(client) == (2, 2)
    first = json.loads(receiver.requests[0].content)
    assert (
        set(first) == {"text", "blocks"} and "x-umai-signature" not in receiver.requests[0].headers
    )
    assert MARKER not in receiver.requests[0].content.decode()
    endpoint = client.get(f"/v1/webhooks/endpoints/{endpoint_id}", headers=_owner()).json()
    assert endpoint["endpoint"]["disabled_reason"] == "gone"


def test_a_host_that_rebinds_to_a_private_address_is_refused_at_delivery(webhooks) -> None:
    client, _, _, dns = webhooks(guarded=True)  # the real transport and its guard
    dns["rebind.example.com"] = ["93.184.216.34"]
    endpoint_id = _add(client, "https://rebind.example.com/hook")["endpoint"]["endpoint_id"]
    dns["rebind.example.com"] = ["169.254.169.254"]
    response = client.post(f"/v1/webhooks/endpoints/{endpoint_id}/test", headers=_owner())
    delivery = response.json()["delivery"]
    assert (delivery["outcome"], delivery["error"], delivery["status_code"]) == (
        "failed",
        "blocked_address",
        None,
    )


# ------------------------------------------------------------------ quota and billing


def test_quota_thresholds_are_sent_once_per_month(webhooks) -> None:
    client, receiver, clock, _ = webhooks()
    _add(client, types=("quota.threshold",))
    usage = [
        {
            "event_id": f"ev_{i}",
            "occurred_at": START,
            "meter": "evaluations",
            "quantity": 1,
            "source": "api",
            "degraded": False,
        }
        for i in range(10)
    ]
    _insert(client, UsageEventRow, usage[:8])
    clock.advance(minutes=1)
    assert _run(client) == (1, 1)
    _insert(client, UsageEventRow, usage[8:])
    clock.advance(minutes=1)
    assert _run(client) == (1, 1)
    clock.advance(minutes=1)
    assert _run(client) == (0, 0)
    thresholds = [
        (b["data"]["threshold_percent"], b["data"]["used"], b["data"]["limit"])
        for b in receiver.bodies()
    ]
    assert thresholds == [(80, 8, 10), (100, 10, 10)]
    assert receiver.bodies()[0]["links"]["console"] == "https://console.example.com/usage"


def test_billing_events_are_sent_once(webhooks) -> None:
    client, receiver, clock, _ = webhooks()
    _add(
        client,
        types=("billing.payment_failed", "billing.subscription_changed"),
    )
    grace = START + timedelta(days=14)
    _insert(
        client,
        SubscriptionRow,
        [
            {
                "id": uuid.uuid4(),
                "provider": "iyzico",
                "parent_reference": "sub-synthetic",
                "reference": "sub-synthetic",
                "pricing_plan_reference": "plan-1",
                "edition": "pro",
                "status": "past_due",
                "grace_until": grace,
                "retry_count": 0,
            }
        ],
    )
    at = START + timedelta(seconds=5)
    _insert(
        client,
        BillingEventRow,
        [
            {"id": uuid.uuid4(), "occurred_at": at, "kind": "account_saved"},  # not sent
            {
                "id": uuid.uuid4(),
                "occurred_at": at,
                "kind": "payment_failed",
                "amount": "1499.00",
                "currency": "TRY",
                "detail": {"error": f"provider text {MARKER}"},
            },
            {
                "id": uuid.uuid4(),
                "occurred_at": at,
                "kind": "plan_changed",
                "detail": {"edition": "pro", "when": "next_period"},
            },
        ],
    )
    clock.advance(minutes=1)
    assert _run(client) == (2, 2)
    clock.advance(minutes=1)
    assert _run(client) == (0, 0)
    bodies = {b["type"]: b["data"] for b in receiver.bodies()}
    assert bodies["billing.payment_failed"]["amount"] == "1499.00"
    assert bodies["billing.payment_failed"]["grace_until"] == grace.isoformat().replace(
        "+00:00", "Z"
    )
    assert bodies["billing.subscription_changed"] == {
        "change": "plan_changed",
        "edition": "pro",
        "effective": "next_period",
        "subscription_status": "past_due",
    }
    assert all(MARKER not in r.content.decode() for r in receiver.requests)


# ------------------------------------------------------------------ disable, delete


def test_turning_off_cancels_pending_and_delete_keeps_the_log(webhooks) -> None:
    client, receiver, clock, _ = webhooks()
    endpoint_id = _add(client)["endpoint"]["endpoint_id"]
    window = datetime(2026, 10, 30, 12, 5, tzinfo=UTC)
    _insert(client, DecisionEventRow, [_decision(window)])
    clock.now = window + timedelta(minutes=6, seconds=1)
    receiver.statuses = [503]
    _run(client)
    off = client.patch(
        f"/v1/webhooks/endpoints/{endpoint_id}", json={"enabled": False}, headers=_owner()
    ).json()["endpoint"]
    assert off["enabled"] is False and off["disabled_reason"] == "manual"
    clock.advance(minutes=2)
    assert client.portal.call(client.app.state.webhooks.deliver_due) == 0
    assert (
        client.delete(f"/v1/webhooks/endpoints/{endpoint_id}", headers=_owner()).status_code == 204
    )
    assert client.get(f"/v1/webhooks/endpoints/{endpoint_id}", headers=_owner()).status_code == 404
    assert len(_deliveries(client, endpoint_id=endpoint_id)) == 1  # the log stays until retention
