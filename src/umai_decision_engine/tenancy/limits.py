"""Per-credential rate limit (ADR-0023 §7): a token bucket per key, in process. Enough for a
single engine node (self-hosted, the starter VM); the SaaS regional stack moves the buckets to
Azure Managed Redis behind the same interface (ADR-0031 §7)."""

from __future__ import annotations

import math
import threading
import time
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


def retry_after_seconds(wait: float) -> int:
    return max(1, math.ceil(wait))
