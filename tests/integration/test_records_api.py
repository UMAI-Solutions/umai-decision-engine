"""Records read API in the policy service (tagged-action 0.4, usage 0.2) and the retention job
(ADR-0041 §5): newest-first pages with a cursor, filters, the event with its L1b outcomes, a
month of usage, one tenant per request, roles (billing reads usage, not decisions). SQLite;
Postgres RLS for the same tables is in tests/db. Synthetic data only."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from tests.integration.test_policy_service_authz import _as, _assertion, client  # noqa: F401
from umai_decision_engine.contracts.events import DecisionEvent, L1bOutcomeRecord, UsageRecord
from umai_decision_engine.control_plane.records import apply_retention
from umai_decision_engine.db.migrate import upgrade
from umai_decision_engine.db.models import DecisionEventRow, L1bOutcomeRow, UsageEventRow
from umai_decision_engine.db.repositories import TenantRepository
from umai_decision_engine.db.session import make_engine, sqlite_url, tenant_session
from umai_decision_engine.events.database import DatabaseRecordWriter

T0 = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)


def event(n: int, *, tenant: str = "default", **overrides) -> DecisionEvent:
    data = {
        "event_id": f"ta-{n:032x}",
        "request_id": f"req-{n}",
        "tenant_id": tenant,
        "occurred_at": T0 + timedelta(minutes=n),
        "source": "api",
        "engine_version": "0.1.0",
        "bundle": {"id": "gr-test", "version": 1},
        "phase": "PRE_LLM",
        "decision": {"action": "ALLOW", "severity": "LOW", "decided_by": "L1a"},
        "latency_ms": {"total": 2.0},
    }
    data.update(overrides)
    return DecisionEvent.model_validate(data)


def usage(n: int, meter: str, quantity: int, *, at: datetime, **overrides) -> UsageRecord:
    return UsageRecord.model_validate(
        {
            "event_id": f"ta-{n:032x}",
            "tenant_id": "default",
            "occurred_at": at,
            "meter": meter,
            "quantity": quantity,
            "source": "api",
            "environment": "live",
            "application_id": "main",
            **overrides,
        }
    )


@pytest.fixture
def store(tmp_path):
    """Writes records into the client's store (the same SQLite file)."""

    def _write(records, *, tenants=("other",)) -> None:
        async def go() -> None:
            engine = make_engine(sqlite_url(tmp_path / "control.sqlite"))
            for slug in tenants:
                await TenantRepository(engine).get_or_create(slug, "eu")
            await DatabaseRecordWriter(engine).write(list(records))
            await engine.dispose()

        asyncio.run(go())

    return _write


def test_events_are_listed_newest_first_page_by_page(client, store) -> None:  # noqa: F811
    store([event(n) for n in range(5)])
    seen, cursor = [], None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = client.get("/v1/decision-events", params=params).json()
        seen += [e["event_id"] for e in page["events"]]
        cursor = page.get("next_cursor")
        if not cursor:
            break
    assert seen == [f"ta-{n:032x}" for n in (4, 3, 2, 1, 0)]


def test_events_can_be_filtered(client, store) -> None:  # noqa: F811
    store(
        [
            event(1),
            event(2, source="gateway", gateway_call_id="gc-1"),
            event(
                3,
                decision={"action": "BLOCK", "severity": "HIGH", "decided_by": "L0"},
                review=True,
                review_reasons=["action"],
            ),
            event(4, phase="POST_LLM"),
        ]
    )

    def ids(**params) -> list[str]:
        page = client.get("/v1/decision-events", params=params).json()
        return [e["event_id"][-1] for e in page["events"]]

    assert ids(action="BLOCK") == ["3"]
    assert ids(action=["BLOCK", "ALLOW_WITH_MODIFICATIONS"]) == ["3"]  # any of them (0.7)
    assert ids(action=["BLOCK", "ALLOW", "BLOCK"]) == ["4", "3", "2", "1"]
    assert ids(source="gateway") == ["2"]
    assert ids(phase="POST_LLM") == ["4"]
    assert ids(review="true") == ["3"]
    assert ids(since=(T0 + timedelta(minutes=3)).isoformat()) == ["4", "3"]
    assert client.get("/v1/decision-events", params={"action": "NOPE"}).status_code == 422
    assert client.get("/v1/decision-events", params={"cursor": "garbage"}).status_code == 400


def test_an_event_comes_with_its_l1b_outcomes(client, store) -> None:  # noqa: F811
    outcome = L1bOutcomeRecord.model_validate(
        {
            "event_id": f"ta-{7:032x}",
            "tenant_id": "default",
            "occurred_at": T0,
            "bundle_id": "gr-test",
            "phase": "PRE_LLM",
            "policy_id": "prompt-attack",
            "policy_version": 1,
            "enforcement": "shadow",
            "outcome": "decided",
            "action": "BLOCK",
            "probability": 0.99,
        }
    )
    store([event(7), outcome])
    detail = client.get(f"/v1/decision-events/ta-{7:032x}").json()
    assert detail["event"]["event_id"] == f"ta-{7:032x}"
    assert detail["event"]["tenant_id"] == "default"
    assert [(o["policy_id"], o["action"]) for o in detail["l1b_outcomes"]] == [
        ("prompt-attack", "BLOCK")
    ]
    assert client.get("/v1/decision-events/ta-missing").status_code == 404


def test_one_tenant_per_request(client, store) -> None:  # noqa: F811
    store([event(1), event(2, tenant="other")])
    mine = client.get("/v1/decision-events").json()["events"]
    theirs = client.get("/v1/decision-events", headers={"X-Umai-Tenant": "other"}).json()["events"]
    assert [e["event_id"] for e in mine] == [f"ta-{1:032x}"]
    assert [e["event_id"] for e in theirs] == [f"ta-{2:032x}"]
    assert client.get(f"/v1/decision-events/ta-{2:032x}").status_code == 404  # not this tenant's


def test_usage_sums_one_calendar_month(client, store) -> None:  # noqa: F811
    store(
        [
            usage(1, "evaluations", 1, at=T0),
            usage(2, "evaluations", 1, at=T0, source="gateway"),
            usage(3, "gateway_calls", 1, at=T0, source="gateway"),
            usage(4, "evaluations", 1, at=T0, environment="test"),
            usage(5, "evaluations", 1, at=datetime(2026, 9, 30, 23, 59, tzinfo=UTC)),  # September
        ]
    )
    summary = client.get("/v1/usage", params={"month": "2026-10"}).json()
    assert summary["month"] == "2026-10" and summary["from"].startswith("2026-10-01")
    assert summary["totals"] == {"evaluations": 3, "gateway_calls": 1}
    by = {(g["meter"], g["source"], g["environment"]): g["quantity"] for g in summary["groups"]}
    assert by[("evaluations", "api", "live")] == 1 and by[("evaluations", "api", "test")] == 1
    assert client.get("/v1/usage", params={"month": "2026-13"}).status_code == 422


def test_billing_reads_usage_but_not_decisions(client) -> None:  # noqa: F811
    billing = _as(_assertion(roles=("billing",), sub="auth0|bill"))
    assert client.get("/v1/usage", headers=billing).status_code == 200
    assert client.get("/v1/decision-events", headers=billing).status_code == 403
    viewer = _as(_assertion(roles=("viewer",), sub="auth0|vic"))
    assert client.get("/v1/decision-events", headers=viewer).status_code == 200


def test_retention_deletes_only_what_is_past_its_period(tmp_path) -> None:
    async def go() -> dict:
        url = sqlite_url(tmp_path / "retention.sqlite")
        upgrade(url)
        engine = make_engine(url)
        tenant = await TenantRepository(engine).get_or_create("default", "eu")
        now = datetime(2026, 12, 1, tzinfo=UTC)
        old, recent = now - timedelta(days=31), now - timedelta(days=5)
        await DatabaseRecordWriter(engine).write(
            [
                event(1, occurred_at=old),
                event(2, occurred_at=recent),
                usage(1, "evaluations", 1, at=old),
                usage(3, "evaluations", 1, at=now - timedelta(days=401)),
            ]
        )
        deleted = await apply_retention(engine, decision_days=30, usage_days=400, now=now)
        async with tenant_session(engine, tenant.id) as session:
            from sqlalchemy import select

            kept = {
                "events": [r.event_id for r in (await session.scalars(select(DecisionEventRow)))],
                "usage": [r.event_id for r in (await session.scalars(select(UsageEventRow)))],
                "outcomes": list(await session.scalars(select(L1bOutcomeRow))),
            }
        await engine.dispose()
        return {"deleted": deleted, **kept}

    result = asyncio.run(go())
    assert result["deleted"] == {
        "decision_events": 1,
        "l1b_outcomes": 0,
        "usage_events": 1,
        "audit_events": 0,
    }
    assert result["events"] == [f"ta-{2:032x}"]  # 31 days old: gone; 5 days: kept
    assert result["usage"] == [f"ta-{1:032x}"]  # usage keeps 400 days


# ------------------------------------------------------------ summary and daily usage (0.5 / 0.3)

WINDOW = {"since": "2026-10-29T00:00:00Z", "until": "2026-11-02T00:00:00Z"}


def _seed_summary(store) -> None:
    store(
        [
            event(
                1,
                decision={"action": "BLOCK", "severity": "HIGH", "decided_by": "L0"},
                entities=[{"type": "TR_TCKN", "count": 2, "distinct": 1}],
                matched=[
                    {"kind": "l0_policy", "id": "identity-data", "version": 1, "action": "BLOCK"}
                ],
            ),
            event(
                2,
                decision={
                    "action": "ALLOW_WITH_MODIFICATIONS",
                    "severity": "LOW",
                    "decided_by": "L0",
                },
                entities=[{"type": "EMAIL_ADDRESS", "count": 1, "distinct": 1}],
                matched=[
                    {
                        "kind": "l0_policy",
                        "id": "contact-data",
                        "version": 1,
                        "action": "ALLOW_WITH_MODIFICATIONS",
                    }
                ],
            ),
            event(3, source="gateway", gateway_call_id="gc-1"),
            event(
                24 * 60 + 5,  # the next UTC day
                decision={
                    "action": "ALLOW_WITH_MODIFICATIONS",
                    "severity": "LOW",
                    "decided_by": "L0",
                },
                entities=[{"type": "EMAIL_ADDRESS", "count": 3, "distinct": 2}],
                matched=[
                    {
                        "kind": "l0_policy",
                        "id": "contact-data",
                        "version": 1,
                        "action": "ALLOW_WITH_MODIFICATIONS",
                    }
                ],
            ),
        ]
    )


def test_the_summary_counts_days_decisions_sources_and_what_was_found(client, store) -> None:  # noqa: F811
    _seed_summary(store)
    summary = client.get("/v1/decision-events/summary", params=WINDOW).json()
    assert summary["total"] == 4
    assert summary["by_action"] == {"BLOCK": 1, "ALLOW_WITH_MODIFICATIONS": 2, "ALLOW": 1}
    assert summary["by_source"] == {"api": 3, "gateway": 1}
    assert summary["by_day"] == [
        {"date": "2026-10-30", "actions": {"ALLOW": 1, "ALLOW_WITH_MODIFICATIONS": 1, "BLOCK": 1}},
        {"date": "2026-10-31", "actions": {"ALLOW_WITH_MODIFICATIONS": 1}},
    ]
    # events in which a type was found, not values: EMAIL_ADDRESS in two events
    assert summary["top_entities"] == [
        {"type": "EMAIL_ADDRESS", "decisions": 2},
        {"type": "TR_TCKN", "decisions": 1},
    ]
    assert summary["top_policies"][0] == {"kind": "l0_policy", "id": "contact-data", "decisions": 2}
    assert (summary["scanned"], summary["truncated"]) == (4, False)


def test_the_summary_takes_the_lists_filters(client, store) -> None:  # noqa: F811
    _seed_summary(store)
    gateway = client.get(
        "/v1/decision-events/summary", params={**WINDOW, "source": "gateway"}
    ).json()
    assert (gateway["total"], gateway["by_source"]) == (1, {"gateway": 1})
    blocked = client.get("/v1/decision-events/summary", params={**WINDOW, "action": "BLOCK"}).json()
    assert blocked["by_action"] == {"BLOCK": 1} and blocked["top_entities"][0]["type"] == "TR_TCKN"
    first_day = client.get(
        "/v1/decision-events/summary",
        params={"since": "2026-10-30T00:00:00Z", "until": "2026-10-31T00:00:00Z"},
    ).json()
    assert [d["date"] for d in first_day["by_day"]] == ["2026-10-30"]


def test_a_large_window_says_its_top_lists_are_from_the_newest_events(client, store) -> None:  # noqa: F811
    _seed_summary(store)
    client.app.state.records._summary_scan = 2  # the limit is 20 000 in service
    summary = client.get("/v1/decision-events/summary", params=WINDOW).json()
    assert summary["total"] == 4  # day and decision counts stay exact
    assert (summary["scanned"], summary["truncated"]) == (2, True)


def overview_events(tenant: str = "default") -> list[DecisionEvent]:
    """Twenty events for the 0.7 summary: totals 1–20 ms, L0 on the even ones, L1b on four;
    application ``main`` 12 (3 blocked), ``hr`` 6, none 2; plus a Playground run (21)."""

    blocked = {"action": "BLOCK", "severity": "HIGH", "decided_by": "L0"}
    events = []
    for n in range(1, 21):
        latency = {"total": float(n)}
        if n % 2 == 0:
            latency["l0"] = n / 10
        if n in (5, 10, 15, 20):
            latency["l1b"] = n * 10.0
        overrides = {"latency_ms": latency, "application_id": "main" if n <= 12 else "hr"}
        if n > 18:
            overrides.update(application_id=None)
        if n in (2, 4, 6):
            overrides["decision"] = blocked
        if overrides["application_id"]:
            overrides["key_id"] = "AbCd1234"
        events.append(event(n, tenant=tenant, **overrides))
    events.append(
        event(
            21,
            tenant=tenant,
            request_id="console-7b0c2f9e-6c1d-4f2a-9a51-3e8d7c6b5a40",
            latency_ms={"total": 999.0},
        )
    )
    return events


def test_the_summary_counts_per_application_and_gives_latency_percentiles(client, store) -> None:  # noqa: F811
    store(overview_events())
    summary = client.get("/v1/decision-events/summary", params=WINDOW).json()
    assert summary["total"] == 21
    assert summary["by_application"] == [
        {
            "application_id": "main",
            "actions": {"ALLOW": 9, "BLOCK": 3},
            "last_event_at": "2026-10-30T08:12:00Z",
        },
        {"application_id": "hr", "actions": {"ALLOW": 6}, "last_event_at": "2026-10-30T08:18:00Z"},
        {"application_id": None, "actions": {"ALLOW": 3}, "last_event_at": "2026-10-30T08:21:00Z"},
    ]
    # Nearest rank over 21 totals (1–20 and 999): the 11th and the 20th value.
    assert summary["latency_ms"] == {
        "total": {"events": 21, "p50": 11.0, "p95": 20.0},
        "l0": {"events": 10, "p50": 1.0, "p95": 2.0},
        "l1b": {"events": 4, "p50": 100.0, "p95": 200.0},
    }
    one_app = client.get(
        "/v1/decision-events/summary", params={**WINDOW, "application_id": "hr"}
    ).json()
    assert [a["application_id"] for a in one_app["by_application"]] == ["hr"]
    assert one_app["latency_ms"]["total"] == {"events": 6, "p50": 15.0, "p95": 18.0}


def test_an_empty_window_has_no_applications_and_no_percentiles(client) -> None:  # noqa: F811
    summary = client.get("/v1/decision-events/summary", params=WINDOW).json()
    assert summary["by_application"] == []
    assert summary["latency_ms"]["total"] == {"events": 0, "p50": None, "p95": None}


def test_playground_runs_can_be_left_out_and_an_api_key_cannot_pass_for_one(client, store) -> None:  # noqa: F811
    store(
        [
            event(1, request_id="console-1b2c"),  # operator token, the console's id: Playground
            event(2, request_id="console-2b2c", key_id="AbCd1234"),  # an API key: never
            event(3, request_id="req-3"),  # operator token, another id: not the Playground
            event(4, request_id="console-4b2c", source="gateway", gateway_call_id="gc-4"),
            event(5, request_id="CONSOLE-5"),  # the prefix is matched exactly, case too
        ]
    )

    def ids(**params) -> list[str]:
        page = client.get("/v1/decision-events", params={**WINDOW, **params}).json()
        return [e["event_id"][-1] for e in page["events"]]

    assert ids(playground="true") == ["1"]
    assert ids(playground="false") == ["5", "4", "3", "2"]
    assert ids() == ["5", "4", "3", "2", "1"]
    summary = client.get(
        "/v1/decision-events/summary", params={**WINDOW, "playground": "false"}
    ).json()
    assert summary["total"] == 4 and summary["latency_ms"]["total"]["events"] == 4
    export = client.get("/v1/decision-events/export.csv", params={**WINDOW, "playground": "true"})
    assert export.headers["X-Umai-Export-Rows"] == "1"


def test_summary_is_not_taken_for_an_event_id_and_needs_the_decisions_permission(client) -> None:  # noqa: F811
    assert client.get("/v1/decision-events/summary").status_code == 200
    billing = _as(_assertion(roles=("billing",), sub="auth0|bill2"))
    assert client.get("/v1/decision-events/summary", headers=billing).status_code == 403


def test_usage_comes_per_day_too(client, store) -> None:  # noqa: F811
    store(
        [
            usage(1, "evaluations", 1, at=T0),
            usage(2, "evaluations", 1, at=T0),
            usage(3, "gateway_calls", 1, at=T0, source="gateway"),
            usage(4, "evaluations", 1, at=T0 + timedelta(days=1)),
        ]
    )
    summary = client.get("/v1/usage", params={"month": "2026-10"}).json()
    assert summary["daily"] == [
        {"date": "2026-10-30", "meters": {"evaluations": 2, "gateway_calls": 1}},
        {"date": "2026-10-31", "meters": {"evaluations": 1}},
    ]
