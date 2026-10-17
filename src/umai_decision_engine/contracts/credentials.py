"""Credentials contract (docs/contracts/credentials.md): API key records and the signed
key-registry snapshot engines load (ADR-0023). Secrets never appear in these models: a key
record carries the SHA-256 of the secret, never the secret."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "0.1"

KeyEnv = Literal["live", "test"]
KeyRegion = Literal["eu", "us", "sh"]
KEY_ID_LENGTH = 8


class Scope(StrEnum):
    """What a key may call. ``bundles:reload`` is a control-plane scope that customer keys
    cannot hold (ADR-0023 §3)."""

    EVALUATE = "evaluate"
    BUNDLES_READ = "bundles:read"
    USAGE_READ = "usage:read"
    BUNDLES_RELOAD = "bundles:reload"


CUSTOMER_SCOPES: frozenset[Scope] = frozenset(
    {Scope.EVALUATE, Scope.BUNDLES_READ, Scope.USAGE_READ}
)
DEFAULT_SCOPES: tuple[Scope, ...] = (Scope.EVALUATE,)


class KeyRegistryEntry(BaseModel):
    """One key as the engine needs it: enough to authenticate and authorise, nothing more."""

    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(min_length=KEY_ID_LENGTH, max_length=KEY_ID_LENGTH)
    secret_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    env: KeyEnv
    region: KeyRegion
    scopes: list[Scope]
    status: Literal["active", "revoked"]
    expires_at: datetime | None = None


class KeyRegistrySnapshot(BaseModel):
    """Per-tenant registry published by the control plane and loaded by engines like a bundle:
    verified, validated, swapped atomically (ADR-0023 §4). ``signature`` is Ed25519 over
    :meth:`signing_payload` with the control plane's bundle-signing key."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["0.1"] = CONTRACT_VERSION
    tenant_id: str = Field(min_length=1, max_length=128)
    generated_at: datetime
    keys: list[KeyRegistryEntry]
    signing_key_id: str | None = None
    signature: str | None = None  # base64

    def signing_payload(self) -> bytes:
        unsigned = self.model_copy(update={"signature": None})
        return unsigned.model_dump_json(exclude_none=True).encode("utf-8")


class ApiKeyRecord(BaseModel):
    """The console's view of a key (control-plane API). The secret is returned once, at
    creation, in a separate field of that response and never stored."""

    model_config = ConfigDict(extra="forbid")

    key_id: str
    tenant_id: str
    name: str
    env: KeyEnv
    region: KeyRegion
    scopes: list[Scope]
    created_by: str
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None

    @property
    def status(self) -> str:
        return "revoked" if self.revoked_at else "active"
