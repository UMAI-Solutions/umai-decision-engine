"""Per-credential rate limit (ADR-0023 §7): a token bucket per key, in process. Enough for a
single engine node (self-hosted, the starter VM); the SaaS regional stack moves the buckets to
Azure Managed Redis behind the same interface (ADR-0031 §7). Failed credentials per source
address (ADR-0046 D3): a bucket per source that only failures drain."""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Hashable
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(self, rate_per_s: float, burst: int, *, clock=time.monotonic) -> None:
        if rate_per_s <= 0 or burst <= 0:
            raise ValueError("rate and burst must be positive")
        self.rate = float(rate_per_s)
        self.burst = float(burst)
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def acquire(self, key: str) -> float | None:
        """``None`` when the request may proceed, otherwise the seconds to wait."""

        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = self._buckets[key] = _Bucket(self.burst, now)
            else:
                bucket.tokens = min(self.burst, bucket.tokens + (now - bucket.updated) * self.rate)
                bucket.updated = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return None
            return (1.0 - bucket.tokens) / self.rate

    def forget_idle(self, older_than_s: float) -> int:
        """Drop buckets not touched for a while (housekeeping; a full bucket is the default)."""

        cutoff = self._clock() - older_than_s
        with self._lock:
            stale = [k for k, b in self._buckets.items() if b.updated < cutoff]
            for key in stale:
                del self._buckets[key]
        return len(stale)


class FailedAuthThrottle:
    """Failed credentials per source (ADR-0046 D3). Each source has a bucket of ``limit``
    tokens refilled over ``window_s``; a failed credential takes one, an empty bucket refuses
    the source's credentials until a token is back. A success never refills it: a caller with
    one valid key could otherwise reset its count between guesses.

    The table holds only sources that failed within the last window (older ones are full again
    and dropped) and at most ``max_sources`` of them; beyond that the oldest is forgotten and
    counted in ``evicted``, so the table cannot be grown without bound."""

    def __init__(
        self, limit: int, window_s: float, *, max_sources: int, clock=time.monotonic
    ) -> None:
        if limit <= 0 or window_s <= 0 or max_sources <= 0:
            raise ValueError("limit, window and table size must be positive")
        self.limit = limit
        self.window_s = float(window_s)
        self.burst = float(limit)
        self.rate = limit / self.window_s
        self.max_sources = max_sources
        self.evicted = 0
        self._clock = clock
        self._buckets: OrderedDict[Hashable, _Bucket] = OrderedDict()  # oldest failure first
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._buckets)

    def blocked(self, source: Hashable) -> float | None:
        """Seconds until ``source`` may present a credential again; ``None``: it may now."""

        bucket = self._buckets.get(source)
        if bucket is None:  # no recent failure: the common case, no lock, no clock
            return None
        tokens = bucket.tokens + (self._clock() - bucket.updated) * self.rate
        if tokens >= 1.0:
            return None
        return (1.0 - tokens) / self.rate

    def failed(self, source: Hashable) -> bool:
        """Count one failed credential; ``True`` when it emptied the source's bucket."""

        now = self._clock()
        with self._lock:
            self._prune(now)
            bucket = self._buckets.pop(source, None)
            if bucket is None:
                while len(self._buckets) >= self.max_sources:
                    self._buckets.popitem(last=False)
                    self.evicted += 1
                bucket = _Bucket(self.burst, now)
            else:
                bucket.tokens = min(self.burst, bucket.tokens + (now - bucket.updated) * self.rate)
                bucket.updated = now
            before = bucket.tokens
            bucket.tokens = max(0.0, bucket.tokens - 1.0)
            self._buckets[source] = bucket  # now the newest
            return before >= 1.0 > bucket.tokens

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s  # a bucket untouched for a window is full again
        while self._buckets:
            source, bucket = next(iter(self._buckets.items()))
            if bucket.updated > cutoff:
                return
            del self._buckets[source]


def retry_after_seconds(wait: float) -> int:
    return max(1, math.ceil(wait))
