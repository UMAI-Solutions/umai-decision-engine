"""API key format (ADR-0023 §2): ``umai_<env>_<region>_<key_id>_<secret>``.

The secret is 32 random bytes in base62 (43 characters); only its SHA-256 is ever stored.
The prefix lets secret scanners and SDK routers recognise a key without a lookup.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import get_args

from umai_decision_engine.contracts.credentials import KEY_ID_LENGTH, KeyEnv, KeyRegion

PREFIX = "umai"
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SECRET_BYTES = 32
_ENVS = get_args(KeyEnv)
_REGIONS = get_args(KeyRegion)
_KEY_RE = re.compile(
    rf"^{PREFIX}_(?P<env>{'|'.join(_ENVS)})_(?P<region>{'|'.join(_REGIONS)})"
    rf"_(?P<key_id>[0-9A-Za-z]{{{KEY_ID_LENGTH}}})_(?P<secret>[0-9A-Za-z]{{20,64}})$"
)


@dataclass(frozen=True)
class ParsedKey:
    env: KeyEnv
    region: KeyRegion
    key_id: str
    secret_sha256: str


@dataclass(frozen=True)
class GeneratedKey:
    presentation: str  # shown once
    key_id: str
    secret_sha256: str


def _base62(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    out = []
    while number:
        number, rem = divmod(number, 62)
        out.append(_ALPHABET[rem])
    return "".join(reversed(out)) or "0"


def sha256_hex(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def generate_key(env: KeyEnv, region: KeyRegion) -> GeneratedKey:
    key_id = "".join(secrets.choice(_ALPHABET) for _ in range(KEY_ID_LENGTH))
    secret = _base62(secrets.token_bytes(_SECRET_BYTES))
    return GeneratedKey(
        presentation=f"{PREFIX}_{env}_{region}_{key_id}_{secret}",
        key_id=key_id,
        secret_sha256=sha256_hex(secret),
    )


def parse_key(value: str) -> ParsedKey | None:
    """``None`` for anything that is not shaped like an API key (so the shared token of
    ADR-0016 can still be tried)."""

    match = _KEY_RE.match(value)
    if match is None:
        return None
    return ParsedKey(
        env=match["env"],  # type: ignore[arg-type]
        region=match["region"],  # type: ignore[arg-type]
        key_id=match["key_id"],
        secret_sha256=sha256_hex(match["secret"]),
    )


def hashes_match(presented_sha256: str, stored_sha256: str) -> bool:
    return hmac.compare_digest(presented_sha256.encode(), stored_sha256.encode())
