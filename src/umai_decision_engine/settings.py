"""Process settings (environment, prefix ``UMAI_DE_``). Secrets live here, never in bundles."""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    l0_threads: int = Field(4, ge=1, le=64)

    # API access. Shared operator token (ADR-0016; self-hosted) and/or per-tenant API keys
    # from signed registry snapshots (ADR-0023). Outside development at least one of the two
    # must be configured: the API refuses to start otherwise. Health routes stay open.
    api_token: SecretStr | None = None
    key_registry_dir: Path | None = None  # control plane writes <tenant>.json here; engine reads
    key_registry_poll_interval_s: float = Field(5.0, ge=0)
    region: Literal["eu", "us", "sh"] = "sh"  # region stamped into new keys; sh = self-hosted
    rate_limit_rps: float | None = Field(20.0, gt=0)  # per credential (ADR-0023 §7); None = off
    rate_limit_burst: int = Field(40, ge=1)

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
        return self

    def tenant_key(self, tenant_id: str) -> bytes:
        encoded = self.tenant_hmac_keys.get(tenant_id)
        if encoded:
            return base64.b64decode(encoded)
        if self.env == "development":
            # Deterministic development key; never valid outside development.
            return hashlib.sha256(f"umai-de-dev-key:{tenant_id}".encode()).digest()
        raise TenantKeyError(f"no HMAC key configured for tenant {tenant_id!r}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
