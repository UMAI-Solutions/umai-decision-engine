"""Credentials contract (docs/contracts/credentials.md): API key records and the signed
key-registry snapshot engines load (ADR-0023). Secrets never appear in these models: a key
record carries the SHA-256 of the secret, never the secret."""

from __future__ import annotations

import ipaddress
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from umai_decision_engine.contracts.entitlements import TenantEntitlements
from umai_decision_engine.contracts.gateway import APPLICATION_ID
from umai_decision_engine.contracts.organizations import Role

# Key-registry snapshot payload version: 0.1 until credentials 0.4, 0.2 in 0.5 (entries may
# carry gateway_id), 0.3 in 0.6 (application_id), 0.4 in 0.7 (entitlements), 0.5 since 0.8
# (allowed_ips). Engines read all.
CONTRACT_VERSION = "0.5"

KeyEnv = Literal["live", "test"]
KeyRegion = Literal["eu", "us", "sh"]
KEY_ID_LENGTH = 8


class Scope(StrEnum):
    """What a key may call. ``bundles:reload`` is a control-plane scope that customer keys
    cannot hold (ADR-0023 §3)."""

    EVALUATE = "evaluate"
    GATEWAY = "gateway"  # the UMAI gateway's OpenAI-compatible surface (credentials 0.4, ADR-0034)
    BUNDLES_READ = "bundles:read"
    USAGE_READ = "usage:read"
    BUNDLES_RELOAD = "bundles:reload"


CUSTOMER_SCOPES: frozenset[Scope] = frozenset(
    {Scope.EVALUATE, Scope.GATEWAY, Scope.BUNDLES_READ, Scope.USAGE_READ}
)
DEFAULT_SCOPES: tuple[Scope, ...] = (Scope.EVALUATE,)

# A key's allowed networks (credentials 0.9, ADR-0046): at most this many CIDRs.
MAX_ALLOWED_IPS = 50


class NetworkListError(ValueError):
    """An allowed-networks list the contract refuses; the message names the entry, never more."""


def canonical_networks(values: list[str]) -> list[str]:
    """IPv4 and IPv6 networks in canonical CIDR form (``203.0.113.0/24``, ``2001:db8::/48``);
    a bare address is a single host (``/32``, ``/128``). Host bits must be zero, so
    ``203.0.113.5/24`` is refused rather than silently widened. Duplicates are refused."""

    if len(values) > MAX_ALLOWED_IPS:
        raise NetworkListError(f"at most {MAX_ALLOWED_IPS} networks per key")
    seen: set[str] = set()
    canonical: list[str] = []
    for index, raw in enumerate(values, start=1):
        value = raw.strip() if isinstance(raw, str) else ""
        try:
            if not value or len(value) > 64 or "%" in value:  # no zone index (fe80::1%eth0)
                raise ValueError
            widened = ipaddress.ip_network(value, strict=False)
        except ValueError:
            raise NetworkListError(f"entry {index} is not an IP address or CIDR network") from None
        if ipaddress.ip_address(value.split("/")[0]) != widened.network_address:
            raise NetworkListError(
                f"entry {index} has host bits set: write {widened} for the whole network, "
                "or the address alone for one host"
            )
        text = str(widened)
        if text in seen:
            raise NetworkListError(f"entry {index} repeats {text}")
        seen.add(text)
        canonical.append(text)
    return canonical


class KeyRegistryEntry(BaseModel):
    """One key as the engine needs it: enough to authenticate and authorise, nothing more."""

    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(min_length=KEY_ID_LENGTH, max_length=KEY_ID_LENGTH)
    secret_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    env: KeyEnv
    region: KeyRegion
    scopes: list[Scope]
    # The key's application (credentials 0.6, ADR-0039); 0.2 snapshots call it gateway_id.
    application_id: str | None = Field(
        None, pattern=APPLICATION_ID, validation_alias=AliasChoices("application_id", "gateway_id")
    )
    status: Literal["active", "revoked"]
    expires_at: datetime | None = None
    # The networks the key may be used from (0.8, ADR-0046); absent: any address.
    allowed_ips: list[str] | None = Field(None, min_length=1, max_length=MAX_ALLOWED_IPS)

    @field_validator("allowed_ips")
    @classmethod
    def _networks(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else canonical_networks(value)


class KeyRegistrySnapshot(BaseModel):
    """Per-tenant registry published by the control plane and loaded by engines like a bundle:
    verified, validated, swapped atomically (ADR-0023 §4). ``signature`` is Ed25519 over
    :meth:`signing_payload` with the control plane's bundle-signing key."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["0.1", "0.2", "0.3", "0.4", "0.5"] = CONTRACT_VERSION
    tenant_id: str = Field(min_length=1, max_length=128)
    generated_at: datetime
    # The tenant's limits (0.4, ADR-0043); absent: no quota, the deployment's size limit.
    entitlements: TenantEntitlements | None = None
    keys: list[KeyRegistryEntry]
    signing_key_id: str | None = None
    signature: str | None = None  # base64

    def signing_payload(self) -> bytes:
        unsigned = self.model_copy(update={"signature": None})
        if self.contract_version != "0.2":
            return unsigned.model_dump_json(exclude_none=True).encode("utf-8")
        # 0.2 snapshots were signed with the field's old name; rebuild that exact payload.
        data = unsigned.model_dump(mode="json", exclude_none=True)
        data["keys"] = [
            {("gateway_id" if k == "application_id" else k): v for k, v in entry.items()}
            for entry in data["keys"]
        ]
        return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


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
    application_id: str | None = None  # the application the key belongs to (0.6, ADR-0039)
    allowed_ips: list[str] | None = None  # networks the key may be used from (0.8, ADR-0046)
    created_by: str
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None

    @property
    def status(self) -> str:
        return "revoked" if self.revoked_at else "active"


# ----------------------------------------------------------- on-behalf-of assertion (0.2)

ACTOR_ASSERTION_TYPE = "umai-actor+jwt"
ACTOR_MAX_LIFETIME_S = 300


class ActorAssertion(BaseModel):
    """Claims of the on-behalf-of assertion the console signs for every policy-service call made
    for a person (ADR-0023 §1): ES256 JWS, ``typ`` ``umai-actor+jwt``, at most five minutes.
    ``tenant_id`` is absent before the person has chosen an organization; ``roles`` only make
    sense with it."""

    model_config = ConfigDict(extra="forbid")

    iss: str = Field(min_length=1, max_length=200)
    aud: str = Field(min_length=1, max_length=200)
    sub: str = Field(min_length=1, max_length=255)
    email: str | None = Field(None, max_length=254)
    email_verified: bool = False  # the ID token's claim (0.3); organizations need it true
    name: str | None = Field(None, max_length=200)  # display name (0.3)
    tenant_id: str | None = Field(None, min_length=1, max_length=128)
    # Informational since 0.3: the policy service reads roles from its own store (ADR-0033 §1).
    roles: list[Role] = Field(default_factory=list)
    session_id: str = Field(min_length=1, max_length=128)
    iat: int
    exp: int
    jti: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _lifetime_and_roles(self) -> ActorAssertion:
        if not 0 < self.exp - self.iat <= ACTOR_MAX_LIFETIME_S:
            raise ValueError(f"lifetime must be 1-{ACTOR_MAX_LIFETIME_S} s")
        if self.roles and self.tenant_id is None:
            raise ValueError("roles need a tenant_id")
        return self
