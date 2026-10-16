"""Per-tenant HMAC of canonical entity values (ADR-0005).

Not Presidio's ``hash`` operator: its random salt prevents correlation and a static salt
makes the ~9·10⁸ valid TCKNs brute-forceable. Keys come from settings, never from bundles.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable

KeyProvider = Callable[[str], bytes]


class TenantKeyError(RuntimeError):
    """No HMAC key is configured for the tenant."""


def value_hash(key: bytes, entity_type: str, canonical_value: str) -> str:
    message = f"{entity_type}\x1f{canonical_value}".encode()
    return hmac.new(key, message, hashlib.sha256).digest()[:16].hex()
