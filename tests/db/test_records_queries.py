"""Records queries on PostgreSQL (tagged-action 0.7, data-export 0.1): the entity-type and policy
filters read JSON arrays with the dialect's own functions, the id search and the usage lines run
through row-level security with the application role. Needs the test Postgres of
tests/db/test_isolation.py; the SQLite twins are in tests/integration/test_data_access.py."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from tests.db.test_isolation import migrated, pg, postgres  # noqa: F401
from tests.integration.test_records_api import T0, event, overview_events, usage
from umai_decision_engine.control_plane.exports import decisions_csv
from umai_decision_engine.control_plane.records import EventFilters, RecordsReader
from umai_decision_engine.db.repositories import TenantRepository
from umai_decision_engine.db.session import tenant_session
from umai_decision_engine.events.database import DatabaseRecordWriter

pytestmark = [postgres, pytest.mark.asyncio]


async def _seed(engine) -> tuple:
    repo = TenantRepository(engine)
    mine = await repo.get_or_create("records-a", "eu")
    theirs = await repo.get_or_create("records-b", "eu")
    await DatabaseRecordWriter(engine).write(
        [
            event(
                1,
                tenant="records-a",
                request_id="req-Satış-1",
                entities=[{"type": "TR_TCKN", "count": 2, "distinct": 1}],
                matched=[
                    {"kind": "l0_policy", "id": "identity-data", "version": 1, "action": "BLOCK"}
                ],
                decision={"action": "BLOCK", "severity": "HIGH", "decided_by": "L0"},
            ),
            event(
                2,
                tenant="records-a",
                source="gateway",
                gateway_call_id="gc-1",
                entities=[{"type": "EMAIL_ADDRESS", "count": 1, "distinct": 1}],
            ),
            event(3, tenant="records-a", source="gateway", gateway_call_id="gc-1"),
            event(4, tenant="records-b", request_id="req-Satış-1"),
            usage(1, "evaluations", 1, at=T0, tenant_id="records-a"),
            usage(2, "evaluations", 1, at=T0 + timedelta(minutes=5), tenant_id="records-a"),
            usage(3, "evaluations", 1, at=T0, tenant_id="records-b"),
        ]
    )
    return mine, theirs


async def _ids(reader: RecordsReader, tenant, **filters) -> list[int]:
    page = await reader.events(tenant, EventFilters(**filters), limit=50)
    return sorted(int(e.event_id[3:], 16) for e in page.events)


async def test_search_and_json_filters_on_postgres(pg) -> None:  # noqa: F811
    mine, theirs = await _seed(pg)
    reader = RecordsReader(pg)
    assert await _ids(reader, mine, q="req-Satış-1") == [1]
    assert await _ids(reader, theirs, q="req-Satış-1") == [4]
    assert await _ids(reader, mine, q="gc-1") == [2, 3]
    assert await _ids(reader, mine, q=f"ta-{3:032x}") == [3]
    assert await _ids(reader, mine, entity_type="TR_TCKN") == [1]
    assert await _ids(reader, mine, entity_type="EMAIL_ADDRESS", q="gc-1") == [2]
    assert await _ids(reader, mine, policy_id="identity-data") == [1]
    assert await _ids(reader, mine, action=("BLOCK", "ALLOW_WITH_WARNINGS")) == [1]
    assert await _ids(reader, theirs, policy_id="identity-data") == []
    summary = await reader.summary(
        mine, EventFilters(since=T0 - timedelta(days=1), entity_type="TR_TCKN")
    )
    assert (summary.total, summary.by_action) == (1, {"BLOCK": 1})
    assert await reader.count_events(mine, EventFilters(source="gateway"), up_to=10) == 2
    chunks = [c async for c in decisions_csv(reader, mine, EventFilters(), max_rows=10)]
    assert b"".join(chunks).decode("utf-8-sig").count("\r\n") == 4  # header and three rows


async def test_usage_lines_on_postgres(pg) -> None:  # noqa: F811
    mine, _ = await _seed(pg)
    lines = await RecordsReader(pg).usage_lines(mine, None, None, up_to=10)
    assert [(line.date, line.meter, line.quantity, line.records) for line in lines] == [
        ("2026-10-30", "evaluations", 2, 2)
    ]


async def test_summary_per_application_latency_and_playground_on_postgres(pg) -> None:  # noqa: F811
    """tagged-action 0.7 under RLS: the same figures as on SQLite (percentile_disc there,
    offsets here), only the caller's tenant, and a query naming another tenant sees nothing."""

    repo = TenantRepository(pg)
    mine = await repo.get_or_create("overview-a", "eu")
    theirs = await repo.get_or_create("overview-b", "eu")
    await DatabaseRecordWriter(pg).write(
        [
            *overview_events("overview-a"),
            event(
                40,
                tenant="overview-b",
                application_id="theirs",
                key_id="ZyXw9876",
                latency_ms={"total": 7.0, "l0": 7.0, "l1b": 7.0},
            ),
        ]
    )
    reader = RecordsReader(pg)
    window = EventFilters(since=T0 - timedelta(days=1), until=T0 + timedelta(days=1))
    summary = await reader.summary(mine, replace(window, playground=False))
    assert [(a.application_id, sum(a.actions.values())) for a in summary.by_application] == [
        ("main", 12),
        ("hr", 6),
        (None, 2),
    ]
    assert summary.by_application[0].last_event_at == T0 + timedelta(minutes=12)
    latency = summary.latency_ms
    assert (latency.total.events, latency.total.p50, latency.total.p95) == (20, 10.0, 19.0)
    assert (latency.l0.events, latency.l0.p50, latency.l0.p95) == (10, 1.0, 2.0)
    assert (latency.l1b.events, latency.l1b.p50, latency.l1b.p95) == (4, 100.0, 200.0)
    playground = await reader.summary(mine, replace(window, playground=True))
    assert playground.total == 1 and playground.latency_ms.total.p95 == 999.0
    other = await reader.summary(theirs, window)
    assert [a.application_id for a in other.by_application] == ["theirs"]
    assert other.latency_ms.total.p50 == 7.0
    # Row-level security, not only the query's own tenant filter: in their session, a query
    # naming my tenant finds none of my events.
    async with tenant_session(pg, theirs.id) as session:
        leaked = await reader._latency(session, mine, window)
    assert leaked.total.events == 0 and leaked.total.p50 is None


async def test_browser_person_and_site_filters_on_postgres(pg) -> None:  # noqa: F811
    """tagged-action 0.8 under RLS: ``user_id`` (an indexed column) and ``destination_app`` (read
    from the JSON object with ``->>``), only in the caller's tenant."""

    repo = TenantRepository(pg)
    mine = await repo.get_or_create("browser-a", "eu")
    theirs = await repo.get_or_create("browser-b", "eu")
    ada, grace = (
        "0f9d2c1e-6a4b-4d8e-9a51-2b7c3e1f0a99",
        "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    )

    def browser(n: int, tenant: str, user: str, app: str):
        return event(
            n,
            tenant=tenant,
            source="browser",
            user_id=user,
            application_id="browser",
            destination={"app": app, "class": "external_llm"},
        )

    await DatabaseRecordWriter(pg).write(
        [
            browser(50, "browser-a", ada, "chatgpt.com"),
            browser(51, "browser-a", ada, "claude.ai"),
            browser(52, "browser-a", grace, "chatgpt.com"),
            browser(53, "browser-b", ada, "chatgpt.com"),
            event(54, tenant="browser-a", destination={"app": "chatgpt.com"}),
        ]
    )
    reader = RecordsReader(pg)
    assert await _ids(reader, mine, source="browser") == [50, 51, 52]
    assert await _ids(reader, mine, user_id=ada) == [50, 51]
    assert await _ids(reader, mine, destination_app="chatgpt.com") == [50, 52, 54]
    assert await _ids(reader, mine, user_id=grace, destination_app="chatgpt.com") == [52]
    assert await _ids(reader, theirs, user_id=ada) == [53]
    page = await reader.events(mine, EventFilters(user_id=ada), limit=50)
    assert {e.user_id for e in page.events} == {ada}
