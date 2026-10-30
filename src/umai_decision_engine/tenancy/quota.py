"""Monthly request quota (ADR-0043 D4–D6). Each process counts in memory: per tenant, month
(UTC) and environment, the usage ledger's total at the last refresh plus what this process
admitted since. Admission takes a lock and adds one, nothing else: no I/O on the request path.
A background loop re-reads the ledger for the tenants that made requests; records written by
another process (the gateway, another engine) are seen after one refresh, so the overshoot is
at most processes × refresh interval × admitted rate.

The ledger is a billing record, not a security control (ADR-0019 §4): when it cannot be read the
last count stays and requests are admitted."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from umai_decision_engine.contracts.entitlements import QuotaEnvironment

log = logging.getLogger("umai.tenancy.quota")

ENVIRONMENTS: tuple[QuotaEnvironment, ...] = ("live", "test")


def month_bounds(month: str | None, now: datetime | None = None) -> tuple[str, datetime, datetime]:
    """``YYYY-MM`` (default: the current month, UTC) → its first instant and the next month's:
    the period a quota covers and the instant it resets."""

    now = now or datetime.now(UTC)
    if month is None:
        month = f"{now:%Y-%m}"
    start = datetime.strptime(month + "-01", "%Y-%m-%d").replace(tzinfo=UTC)
    end = (start + timedelta(days=32)).replace(day=1)
    return month, start, end


def _month(now: datetime) -> str:
    """``YYYY-MM`` of an instant: the counter's key, cheap enough for every request."""

    return f"{now.year:04d}-{now.month:02d}"


class MonthCounts(Protocol):
    async def month(self, slug: str, start: datetime, end: datetime) -> dict[str, int]: ...


@dataclass
class _Count:
    ledger: int = 0  # the ledger's total at the last refresh
    local: int = 0  # admitted by this process since


@dataclass(frozen=True)
class QuotaRefusal:
    limit: int
    used: int
    environment: QuotaEnvironment
    resets_at: datetime


def quota_environment(key_env: str | None) -> QuotaEnvironment:
    """Test keys have their own quota; everything else is live (D6)."""

    return "test" if key_env == "test" else "live"


class QuotaCounter:
    def __init__(
        self,
        ledger: MonthCounts | None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ledger = ledger
        self._clock = clock
        self._counts: dict[tuple[str, str, QuotaEnvironment], _Count] = {}
        self._active: set[str] = set()  # tenants with a limit that asked since the last refresh
        self._lock = threading.Lock()

    def admit(
        self, tenant: str, environment: QuotaEnvironment, limit: int | None
    ) -> QuotaRefusal | None:
        """``None``: admitted and counted. Without a limit nothing is refused and the ledger
        alone counts (metered, D3)."""

        if limit is None:
            return None
        now = self._clock()
        with self._lock:
            count = self._counts.setdefault((tenant, _month(now), environment), _Count())
            self._active.add(tenant)
            used = count.ledger + count.local
            if used < limit:
                count.local += 1
                return None
        return QuotaRefusal(limit, used, environment, month_bounds(None, now)[2])

    def used(self, tenant: str, environment: QuotaEnvironment) -> int:
        with self._lock:
            count = self._counts.get((tenant, _month(self._clock()), environment))
            return count.ledger + count.local if count else 0

    def watch(self, tenants: Iterable[str]) -> None:
        """Read these tenants at the next refresh (at startup: the tenants with a limit, so a
        restart does not forget a used quota)."""

        with self._lock:
            self._active.update(tenants)

    async def refresh(self) -> int:
        """Re-read the month count of every tenant that asked since the last refresh; returns
        how many were read."""

        if self._ledger is None:
            return 0
        with self._lock:
            tenants, self._active = self._active, set()
        month, start, end = month_bounds(None, self._clock())
        read = 0
        for tenant in sorted(tenants):
            with self._lock:
                before = {
                    env: self._counts.get((tenant, month, env), _Count()).local
                    for env in ENVIRONMENTS
                }
            try:
                counts = await self._ledger.month(tenant, start, end)
            except Exception as exc:  # keep the last count, admit (ADR-0019 §4)
                log.warning("quota.refresh_failed tenant=%s error=%s", tenant, type(exc).__name__)
                with self._lock:
                    self._active.add(tenant)
                continue
            with self._lock:
                for env in ENVIRONMENTS:
                    count = self._counts.setdefault((tenant, month, env), _Count())
                    count.ledger = int(counts.get(env, 0))
                    # what this process admitted while the query ran is not in it yet
                    count.local = max(0, count.local - before[env])
            read += 1
        with self._lock:  # earlier months are over
            for key in [k for k in self._counts if k[1] != month]:
                del self._counts[key]
        return read

    async def run(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self.refresh()
            except Exception:  # the counts stay; the next round tries again
                log.exception("quota refresh failed")
