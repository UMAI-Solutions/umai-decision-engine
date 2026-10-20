"""Per-segment detection cache (ADR-0037).

A conversation is resent in full on every turn, so without a cache L0 rescans every earlier
message each time and a turn costs time in proportion to the whole history. The cache keeps,
per scanned segment (one message or attachment part), what L0 found in it: entity types, spans,
scores and value hashes. It never holds the text or an entity's value; its keys are HMACs under
a secret that lives only in this process. A result is reused only for the same text, tenant
hashing key, detector configuration and language, so a policy change can never serve a stale
result: the new configuration simply misses.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from collections import OrderedDict

from umai_decision_engine.contracts.bundle import L0Config


class DetectionCache[T]:
    """A bounded, thread-safe LRU map from (scope, segment text) to a scan result."""

    def __init__(self, max_entries: int) -> None:
        if max_entries < 1:
            raise ValueError("a detection cache needs room for at least one entry")
        self._max = max_entries
        self._entries: OrderedDict[bytes, T] = OrderedDict()
        self._lock = threading.Lock()
        self._secret = secrets.token_bytes(32)
        self.hits = 0
        self.misses = 0

    def scope(self, key: bytes, config: L0Config, language: str) -> bytes:
        """Everything a scan depends on besides the text: the tenant's hashing key (value
        hashes), the detector configuration of the phase and the language."""

        digest = hmac.new(self._secret, digestmod=hashlib.sha256)
        for part in (key, config.model_dump_json().encode(), language.encode()):
            digest.update(len(part).to_bytes(4, "big"))
            digest.update(part)
        return digest.digest()

    def _key(self, scope: bytes, text: str) -> bytes:
        return hmac.new(
            self._secret, scope + text.encode("utf-8", "surrogatepass"), hashlib.sha256
        ).digest()

    def get(self, scope: bytes, text: str) -> T | None:
        key = self._key(scope, text)
        with self._lock:
            value = self._entries.get(key)
            if value is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return value

    def put(self, scope: bytes, text: str, value: T) -> None:
        key = self._key(scope, text)
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
