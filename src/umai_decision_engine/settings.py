"""Process settings (environment, prefix ``UMAI_DE_``). Secrets live here, never in bundles."""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from umai_decision_engine.l0.hashing import TenantKeyError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UMAI_DE_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    max_timeout_ms: int = Field(3000, ge=100, le=60_000)
    default_timeout_ms: int = Field(1500, ge=50, le=60_000)

    # policy bundles (ADR-0012)
    policy_dir: Path = Path("policies")
    require_signed_bundles: bool | None = None  # None → True outside development
    bundle_public_keys: dict[str, str] = Field(default_factory=dict)  # key_id → base64 Ed25519
    bundle_poll_interval_s: float = Field(5.0, ge=0)  # 0 disables polling
    allow_inline_bundles: bool | None = None  # None → True only in development

    # L0
    tenant_hmac_keys: dict[str, str] = Field(default_factory=dict)  # tenant → base64 key
    # Single-VM and self-hosted installs whose organizations are created at runtime: a tenant
    # without an entry above gets a key derived from this secret (HKDF-SHA256, info names the
    # tenant), so a new organization needs no restart (ADR-0032 amendment 2026-10-19). UMAI
    # Cloud generates and wraps one key per tenant instead (ADR-0027 §5).
    tenant_hmac_master_key: SecretStr | None = None  # base64, at least 32 bytes
    l0_threads: int = Field(4, ge=1, le=64)
    # Per-process cache of per-message L0 results (ADR-0037): a resent conversation is not
    # rescanned. Types, spans and value hashes only, never text. 0 disables it.
    l0_cache_entries: int = Field(20_000, ge=0, le=1_000_000)

    # API access. Shared operator token (ADR-0016; self-hosted) and/or per-tenant API keys
    # from signed registry snapshots (ADR-0023). Outside development at least one of the two
    # must be configured: the API refuses to start otherwise. Health routes stay open.
    api_token: SecretStr | None = None
    key_registry_dir: Path | None = None  # control plane writes <tenant>.json here; engine reads
    key_registry_poll_interval_s: float = Field(5.0, ge=0)
    region: Literal["eu", "us", "sh"] = "sh"  # region stamped into new keys; sh = self-hosted
    rate_limit_rps: float | None = Field(20.0, gt=0)  # per credential (ADR-0023 §7); None = off
    rate_limit_burst: int = Field(40, ge=1)
    # Prompt size in characters over all request content; above it 413, never a partial scan
    # (ADR-0019 §4 and amendment, Q19 answered). Deployment-wide until entitlements carry a
    # per-tenant `max_input_chars` (Community 32 000, about the 8 000 tokens planned).
    max_input_chars: int = Field(32_000, ge=1_000, le=1_000_000)

    # Policy service (control plane, ADR-0022): drafts, publish, activate. Separate process.
    control_api_token: SecretStr | None = None  # required outside development
    control_db_path: Path = Path(".data/control.sqlite")  # SQLite when database_url is unset
    # Tenant-scoped store (ADR-0025). Application role URL (no BYPASSRLS) and the owner URL
    # used only for migrations. postgresql+asyncpg://… in SaaS; unset → SQLite (control_db_path).
    database_url: SecretStr | None = None
    database_admin_url: SecretStr | None = None
    default_tenant_id: str = "default"  # control-plane requests without X-Umai-Tenant
    signing_key_file: Path | None = None  # Ed25519 private key (base64) for published manifests
    signing_key_id: str | None = None
    # On-behalf-of assertions from the console (ADR-0023 §1, contract credentials 0.2).
    # required: tenant actions need an assertion naming the person; the control token alone may
    # only run operator actions (tenants, keys). optional: without an assertion the control token
    # acts as the operator for every action (single-operator installs, development). None →
    # required in production, optional elsewhere.
    actor_assertion: Literal["required", "optional"] | None = None
    actor_public_keys: dict[str, str] = Field(default_factory=dict)  # kid → base64 P-256 SPKI
    actor_issuer: str = "umai-console"
    actor_audience: str = "umai-policy-service"
    # Organizations (ADR-0033). Limits are settings until entitlements reach the control plane.
    max_owned_community_organizations: int = Field(1, ge=0)  # §4, Q40 answered (A15)
    max_pending_invitations: int = Field(20, ge=1)  # per tenant, §5
    starter_guardrail: str | None = "gr-demo-data-protection"  # copied into new organizations
    disposable_email_domains: list[str] = Field(default_factory=list)  # added to the built-in list

    # Gateway (ADR-0034, ADR-0038, contract gateway-api). Gateways: one YAML file per tenant,
    # <gateways_dir>/<tenant_id>.yaml, hot-reloaded. Provider base URLs must match an
    # allow-list entry: scheme://host-glob[:port]; http only where an entry says so.
    gateways_dir: Path = Path("gateways")
    gateways_poll_interval_s: float = Field(5.0, ge=0)  # 0 disables polling
    gateway_upstream_allowlist: list[str] = Field(
        default_factory=lambda: [
            "https://api.openai.com",
            "https://*.openai.azure.com",
            "https://*.services.ai.azure.com",
            "https://*.cognitiveservices.azure.com",
        ]
    )
    # Provider keys the deployment holds for routes with credential "secret" (name → key).
    # Never logged; never sent anywhere but the route's upstream (ADR-0035 §4).
    gateway_provider_keys: dict[str, SecretStr] = Field(default_factory=dict)
    gateway_max_request_bytes: int = Field(16_000_000, ge=10_000, le=200_000_000)
    # Characters the gateway scans per call: the whole history, resent every turn (gateway-api
    # 0.8). Larger than max_input_chars because a conversation grows; the L0 cache keeps a
    # turn's cost to its new messages (ADR-0037).
    gateway_max_input_chars: int = Field(256_000, ge=1_000, le=4_000_000)
    # Streaming "window" mode: characters held back so an entity or placeholder split across
    # chunks is never released half-checked (contract gateway-api, Streaming).
    gateway_stream_holdback_chars: int = Field(128, ge=32, le=4096)
    gateway_stream_step_chars: int = Field(32, ge=1, le=4096)

    # development endpoints
    expose_detect_endpoint: bool | None = None  # None → True only in development

    @model_validator(mode="after")
    def _environment_defaults(self) -> Settings:
        dev = self.env == "development"
        if self.require_signed_bundles is None:
            self.require_signed_bundles = not dev
        if self.allow_inline_bundles is None:
            self.allow_inline_bundles = dev
        if self.expose_detect_endpoint is None:
            self.expose_detect_endpoint = dev
        if self.actor_assertion is None:
            self.actor_assertion = "required" if self.env == "production" else "optional"
        if self.tenant_hmac_master_key is not None and len(self._master_key()) < 32:
            raise ValueError("tenant_hmac_master_key must decode to at least 32 bytes")
        return self

    def _master_key(self) -> bytes:
        assert self.tenant_hmac_master_key is not None
        return base64.b64decode(self.tenant_hmac_master_key.get_secret_value())

    def tenant_key(self, tenant_id: str) -> bytes:
        encoded = self.tenant_hmac_keys.get(tenant_id)
        if encoded:
            return base64.b64decode(encoded)
        if self.tenant_hmac_master_key is not None:
            info = b"umai-tenant-hmac/v1:" + tenant_id.encode("utf-8")
            return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(
                self._master_key()
            )
        if self.env == "development":
            # Deterministic development key; never valid outside development.
            return hashlib.sha256(f"umai-de-dev-key:{tenant_id}".encode()).digest()
        raise TenantKeyError(f"no HMAC key configured for tenant {tenant_id!r}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
