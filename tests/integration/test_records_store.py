"""Records in the tenant store (ADR-0041, ADR-0042) on SQLite with the real migrations: the
writer's rows, idempotent retries, unknown tenants, and the engine API end to end (evaluate →
queue → writer → rows, flushed on shutdown). Postgres RLS for the same tables is in
tests/db/test_isolation.py. Synthetic data only."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.conftest import bundle_yaml
from umai_decision_engine.api.app import create_app
from umai_decision_engine.contracts.events import DecisionEvent, L1bOutcomeRecord, UsageRecord
from umai_decision_engine.db.migrate import upgrade
from umai_decision_engine.db.models import DecisionEventRow, L1bOutcomeRow, UsageEventRow
from umai_decision_engine.db.repositories import TenantRepository
from umai_decision_engine.db.session import make_engine, sqlite_url, tenant_session
from umai_decision_engine.events.database import DatabaseRecordWriter
from umai_decision_engine.settings import Settings

DOCS = Path(__file__).resolve().parents[2] / "docs" / "contracts"
TCKN = "10000000146"


def _examples(name: str) -> list[dict]:
    text = (DOCS / name).read_text(encoding="utf-8")
    return [json.loads(b) for b in re.findall(r"```json\n(.*?)```", text, re.S)]


@pytest.fixture
def db_url(tmp_path) -> str:
    url = sqlite_url(tmp_path / "records.sqlite")
    upgrade(url)
    return url


async def _count(engine, tenant, model) -> int:
    async with tenant_session(engine, tenant) as session:
        return await session.scalar(
            select(func.count()).select_from(model).where(model.tenant_id == tenant)
        )


async def test_the_contract_examples_are_written_once_even_when_retried(db_url) -> None:
    event, outcome, _page = _examples("tagged-action.md")
    usage, _summary = _examples("usage.md")
    batch = [
        DecisionEvent.model_validate(event),
        L1bOutcomeRecord.model_validate(outcome),
        UsageRecord.model_validate(usage),
    ]
    engine = make_engine(db_url)
    tenant = await TenantRepository(engine).get_or_create("tenant-demo", "eu")
    writer = DatabaseRecordWriter(engine)
    assert (await writer.write(batch)).written == 3
    assert (await writer.write(batch)).written == 3  # a retried batch: ignored, not doubled
    for model in (DecisionEventRow, L1bOutcomeRow, UsageEventRow):
        assert await _count(engine, tenant.id, model) == 1
    async with tenant_session(engine, tenant.id) as session:
        row = await session.scalar(select(DecisionEventRow))
    assert (row.action, row.decided_by, row.bundle_version) == ("BLOCK", "L0", 3)
    assert row.destination == {"app": "chatgpt.com", "class": "external_llm"}
    assert row.entities == [{"type": "TR_TCKN", "count": 2, "distinct": 2}]
    await writer.aclose()


async def test_records_of_a_tenant_the_store_does_not_know_are_counted_not_written(db_url) -> None:
    usage, _summary = _examples("usage.md")
    writer = DatabaseRecordWriter(make_engine(db_url))
    result = await writer.write([UsageRecord.model_validate({**usage, "tenant_id": "nobody"})])
    assert (result.written, result.unknown_tenant) == (0, 1)
    await writer.aclose()


def test_the_engine_api_records_each_evaluation_in_the_store(write_bundle, detector, db_url):
    write_bundle(bundle_yaml(), active=1)
    engine = make_engine(db_url)
    tenant = asyncio.run(TenantRepository(engine).get_or_create("tenant-a", "eu"))
    settings = Settings(
        env="development",
        policy_dir=write_bundle.root,
        default_tenant_id="tenant-a",
        bundle_poll_interval_s=0,
        l0_threads=1,
        records_sink="database",
        database_url=db_url,
        records_flush_interval_ms=10,
    )
    body = {
        "contract_version": "0.12",
        "request_id": "req-records",
        "timestamp": "2026-10-26T09:00:00Z",
        "tenant_id": "tenant-a",
        "bundle": {"id": "gr-test"},
        "phase": "PRE_LLM",
        "content": {
            "messages": [{"role": "user", "content": f"TC kimlik {TCKN}"}],
            "focus": "LAST_USER_MESSAGE",
        },
    }
    with TestClient(create_app(settings, detector=detector)) as client:
        response = client.post("/v1/evaluate", json=body)
        assert response.status_code == 200
        event_id = response.json()["event_id"]
    # the app has shut down: the queue was flushed

    async def rows():
        async with tenant_session(engine, tenant.id) as session:
            events = (await session.scalars(select(DecisionEventRow))).all()
            usage = (await session.scalars(select(UsageEventRow))).all()
        await engine.dispose()
        return events, usage

    events, usage = asyncio.run(rows())
    assert [(e.event_id, e.action, e.source) for e in events] == [(event_id, "BLOCK", "api")]
    assert [(u.event_id, u.meter, u.quantity) for u in usage] == [(event_id, "evaluations", 1)]
    assert TCKN not in json.dumps([e.entities + e.tags + e.matched for e in events], default=str)


def test_database_records_need_a_database_url(write_bundle, detector) -> None:
    write_bundle(bundle_yaml(), active=1)
    settings = Settings(
        env="development", policy_dir=write_bundle.root, l0_threads=1, records_sink="database"
    )
    with (
        pytest.raises(RuntimeError, match="UMAI_DE_DATABASE_URL"),
        TestClient(create_app(settings, detector=detector)),
    ):
        pass
