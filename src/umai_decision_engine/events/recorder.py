"""What the pipeline hands records to (ADR-0042). ``record`` never blocks and never raises:
a record must not delay or change a decision. The queue recorder drains in a background task,
in batches, retrying a failed batch with backoff; when its queue is full a new record is
dropped and counted (rule 4: an explicit, recorded behaviour, never a silent one)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from umai_decision_engine.contracts.events import Record

logger = logging.getLogger("umai.de.records")

_DROP_LOG_INTERVAL_S = 60.0
_POLL_S = 1.0  # how often an idle writer wakes up to check for shutdown and stats


class Recorder(Protocol):
    def record(self, record: Record) -> None: ...


class NullRecorder:
    """Records nothing: golden tests, evals, the policy service and ``records_sink=none``."""

    def record(self, record: Record) -> None:
        return None


class MemoryRecorder:
    """Keeps records in a list: tests and the leak suite."""

    def __init__(self) -> None:
        self.records: list[Record] = []

    def record(self, record: Record) -> None:
        self.records.append(record)


@dataclass
class WriteResult:
    written: int = 0
    unknown_tenant: int = 0  # records of a tenant the store does not know: dropped


class RecordWriter(Protocol):
    async def write(self, batch: list[Record]) -> WriteResult: ...

    async def aclose(self) -> None: ...


class QueueRecorder:
    def __init__(
        self,
        writer: RecordWriter,
        *,
        queue_size: int = 10_000,
        batch_size: int = 200,
        flush_interval_s: float = 0.5,
        write_timeout_s: float = 5.0,
        stats_interval_s: float = 60.0,
        backoff_initial_s: float = 0.5,
        backoff_max_s: float = 30.0,
    ) -> None:
        self._writer = writer
        self._queue: asyncio.Queue[Record] = asyncio.Queue(maxsize=queue_size)
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._write_timeout_s = write_timeout_s
        self._stats_interval_s = stats_interval_s
        self._backoff_initial_s = backoff_initial_s
        self._backoff_max_s = backoff_max_s
        self._task: asyncio.Task | None = None
        self._closing = False
        self._batch: list[Record] = []  # taken from the queue, not yet written
        self._last_drop_log = float("-inf")
        self._last_stats = time.monotonic()
        self.written = 0
        self.failed_batches = 0
        self.dropped: Counter[str] = Counter()

    @property
    def backlog(self) -> int:
        return self._queue.qsize() + len(self._batch)

    # ------------------------------------------------------------------ request path

    def record(self, record: Record) -> None:
        if self._closing:
            self._drop("shutdown")
            return
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._drop("queue_full")

    def _drop(self, reason: str, count: int = 1) -> None:
        if count <= 0:
            return
        self.dropped[reason] += count
        now = time.monotonic()
        if now - self._last_drop_log >= _DROP_LOG_INTERVAL_S:
            self._last_drop_log = now
            logger.warning(
                "records.dropped reason=%s dropped=%s backlog=%d",
                reason,
                dict(self.dropped),
                self.backlog,
            )

    # ------------------------------------------------------------------ writer task

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def aclose(self, timeout_s: float = 5.0) -> None:
        """Stop taking records, write what is queued within ``timeout_s``, drop the rest."""

        self._closing = True
        if self._task is not None:
            if self.backlog:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(self._task), timeout_s)
            # idle (nothing queued or in flight) or past the deadline: stop now
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._drop("shutdown", self.backlog)
        self._batch = []
        while not self._queue.empty():
            self._queue.get_nowait()
        self._log_stats()

    async def writer_aclose(self) -> None:
        await self._writer.aclose()

    async def _run(self) -> None:
        while True:
            await self._take()
            if self._batch:
                await self._write(self._batch)
                self._batch = []
            if time.monotonic() - self._last_stats >= self._stats_interval_s:
                self._log_stats()
            if self._closing and self._queue.empty():
                return

    async def _take(self) -> None:
        """Fill ``self._batch``: what is queued now, then what arrives within the flush
        interval, up to the batch size. A record counts as in flight once it leaves the queue."""

        try:
            self._batch = [await asyncio.wait_for(self._queue.get(), _POLL_S)]
        except TimeoutError:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (0 if self._closing else self._flush_interval_s)
        while len(self._batch) < self._batch_size:
            if not self._queue.empty():
                self._batch.append(self._queue.get_nowait())
                continue
            left = deadline - loop.time()
            if left <= 0:
                break
            try:
                self._batch.append(await asyncio.wait_for(self._queue.get(), left))
            except TimeoutError:
                break

    async def _write(self, batch: list[Record]) -> None:
        delay = self._backoff_initial_s
        while True:
            try:
                result = await asyncio.wait_for(self._writer.write(batch), self._write_timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the store is down or slow: keep the batch, back off
                self.failed_batches += 1
                logger.warning(
                    "records.write_failed batch=%d error=%s retry_in_s=%s backlog=%d",
                    len(batch),
                    type(exc).__name__,
                    delay,
                    self.backlog,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._backoff_max_s)
                continue
            self.written += result.written
            self._drop("unknown_tenant", result.unknown_tenant)
            return

    def _log_stats(self) -> None:
        self._last_stats = time.monotonic()
        logger.info(
            "records.stats written=%d dropped=%s failed_batches=%d backlog=%d",
            self.written,
            dict(self.dropped),
            self.failed_batches,
            self.backlog,
        )
