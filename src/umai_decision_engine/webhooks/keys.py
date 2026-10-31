"""Webhook secrets and URL encryption from the deployment's master key (ADR-0048 §6–§7).

Nothing secret is stored: an endpoint's signing secret is derived from the master key, the tenant,
the endpoint and its secret version, and the key that encrypts endpoint URLs is derived from the
master key too. Changing the master key changes every secret and makes stored URLs unreadable."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SECRET_PREFIX = "whsec_"
_SEALED_PREFIX = "v1:"


def _derive(master: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(master)


def signing_secret(master: bytes, tenant: uuid.UUID, endpoint: uuid.UUID, version: int) -> str:
    """The ``whsec_…`` secret of one endpoint and secret version; receivers use the whole string
    as the HMAC key."""

    info = f"umai-webhook-secret/v1:{tenant}:{endpoint}:{version}".encode()
    raw = _derive(master, info)
    return SECRET_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def signature_header(secrets: list[str], body: bytes, timestamp: int) -> str:
    """``t=<unix>,v1=<hex>[,v1=<hex>]``: one ``v1`` per secret (the current one first; during a
    rotation's overlap also the previous one)."""

    signed = f"{timestamp}.".encode() + body
    parts = [f"t={timestamp}"]
    for secret in secrets:
        parts.append("v1=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest())
    return ",".join(parts)


class UnreadableUrl(Exception):
    """The stored URL does not decrypt: another master key, or a tampered row."""


class UrlCipher:
    """AES-256-GCM over the endpoint URL; the tenant and endpoint ids are associated data, so a
    sealed URL copied to another row does not open."""

    def __init__(self, master: bytes) -> None:
        self._aead = AESGCM(_derive(master, b"umai-webhook-url/v1"))

    @staticmethod
    def _aad(tenant: uuid.UUID, endpoint: uuid.UUID) -> bytes:
        return f"{tenant}:{endpoint}".encode()

    def seal(self, url: str, tenant: uuid.UUID, endpoint: uuid.UUID) -> str:
        nonce = os.urandom(12)
        sealed = self._aead.encrypt(nonce, url.encode(), self._aad(tenant, endpoint))
        return _SEALED_PREFIX + base64.b64encode(nonce + sealed).decode()

    def open(self, sealed: str, tenant: uuid.UUID, endpoint: uuid.UUID) -> str:
        if not sealed.startswith(_SEALED_PREFIX):
            raise UnreadableUrl("unknown format")
        try:
            raw = base64.b64decode(sealed[len(_SEALED_PREFIX) :], validate=True)
            return self._aead.decrypt(raw[:12], raw[12:], self._aad(tenant, endpoint)).decode()
        except (ValueError, InvalidTag) as exc:
            raise UnreadableUrl("does not decrypt with this key") from exc
