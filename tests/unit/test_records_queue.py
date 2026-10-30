"""The records write path (ADR-0042): batches, retries that keep the batch, explicit drops
(queue full, unknown tenant, shutdown), and a flush on close. A fake writer; the database
writer has its own tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from umai_decision_engine.contracts.events import UsageRecord
from umai_decision_engine.events.recorder import QueueRecorder, WriteResult


def usage(n: int, tenant: str = "tenant-a") -> UsageRecord:
    return UsageRecord(
        event_id=f"ta-{n:032x}",
        tenant_id=tenant,
        occurred_at=datetime(2026, 10, 26, tzinfo=UTC),
        meter="evaluations",
        quantity=1,
        source="api",
    )


class Writer:
    def __init__(self, fail: int = 0, forever: bool = False, unknown: set[str] | None = None):
        self.batches: list[list[UsageRecord]] = []
        self.fail = fail
        self.forever = forever
        self.unknown = unknown or set()
        self.closed = False

    async def write(self, batch):
        if self.forever or self.fail:
            self.fail = max(self.fail - 1, 0)
            raise ConnectionError("store down")
        self.batches.append(list(batch))
        skipped = sum(r.tenant_id in self.unknown for r in batch)
        return WriteResult(written=len(batch) - skipped, unknown_tenant=skipped)

    async def aclose(self) -> None:
        self.closed = True


def recorder(writer: Writer, **kwargs) -> QueueRecorder:
    options = {"flush_interval_s": 0.01, "backoff_initial_s": 0.01, "backoff_max_s": 0.02}
    return QueueRecorder(writer, **{**options, **kwargs})


async def test_queued_records_are_written_in_batches_and_flushed_on_close() -> None:
    writer = Writer()
    rec = recorder(writer, batch_size=2)
    for n in range(5):
        rec.record(usage(n))
    rec.start()
    await rec.aclose(timeout_s=2)
    assert [len(b) for b in writer.batches] == [2, 2, 1]
    assert rec.written == 5 and not rec.dropped and rec.backlog == 0


async def test_a_full_queue_drops_and_counts_never_blocks() -> None:
    rec = recorder(Writer(), queue_size=2)
    for n in range(3):
        rec.record(usage(n))  # not started: nothing drains
    assert rec.dropped["queue_full"] == 1 and rec.backlog == 2


async def test_a_failed_batch_is_retried_whole_until_the_store_is_back() -> None:
    writer = Writer(fail=2)
    rec = recorder(writer)
    rec.record(usage(1))
    rec.record(usage(2))
    rec.start()
    await rec.aclose(timeout_s=2)
    assert rec.failed_batches == 2
    assert [[r.event_id for r in b] for b in writer.batches] == [
        [usage(1).event_id, usage(2).event_id]
    ]
    assert rec.written == 2 and not rec.dropped


async def test_what_cannot_be_written_by_shutdown_is_dropped_and_counted() -> None:
    rec = recorder(Writer(forever=True))
    for n in range(3):
        rec.record(usage(n))
    rec.start()
    await asyncio.sleep(0.05)
    await rec.aclose(timeout_s=0.05)
    assert rec.dropped["shutdown"] == 3 and rec.written == 0 and rec.backlog == 0
    rec.record(usage(9))  # after close: dropped, not queued
    assert rec.dropped["shutdown"] == 4


async def test_records_of_an_unknown_tenant_are_dropped_and_counted() -> None:
    rec = recorder(Writer(unknown={"tenant-x"}))
    rec.record(usage(1))
    rec.record(usage(2, tenant="tenant-x"))
    rec.start()
    await rec.aclose(timeout_s=2)
    assert rec.written == 1 and rec.dropped["unknown_tenant"] == 1


async def test_the_writer_is_closed_with_the_recorder() -> None:
    writer = Writer()
    rec = recorder(writer)
    rec.start()
    await rec.aclose(timeout_s=1)
    await rec.writer_aclose()
    assert writer.closed


@pytest.mark.parametrize("count", [0, 1])
async def test_stats_are_logged_with_counts_only(caplog, count) -> None:
    rec = recorder(Writer())
    for n in range(count):
        rec.record(usage(n))
    rec.start()
    with caplog.at_level("INFO", logger="umai.de.records"):
        await rec.aclose(timeout_s=1)
    [line] = [r.getMessage() for r in caplog.records if "records.stats" in r.getMessage()]
    assert f"written={count}" in line and "tenant-a" not in line
