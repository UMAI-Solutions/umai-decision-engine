"""Records queries on PostgreSQL (tagged-action 0.6, data-export 0.1): the entity-type and policy
filters read JSON arrays with the dialect's own functions, the id search and the usage lines run
through row-level security with the application role. Needs the test Postgres of
tests/db/test_isolation.py; the SQLite twins are in tests/integration/test_data_access.py."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.db.test_isolation import migrated, pg, postgres  # noqa: F401
from tests.integration.test_records_api import T0, event, usage
from umai_decision_engine.control_plane.exports import decisions_csv
from umai_decision_engine.control_plane.records import EventFilters, RecordsReader
from umai_decision_engine.db.repositories import TenantRepository
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
