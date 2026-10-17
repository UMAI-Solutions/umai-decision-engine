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

    # API access (ADR-0016): bearer token on every /v1 route; health routes stay open.
    # Required outside development: the API refuses to start without it.
    api_token: SecretStr | None = None

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
