"""Per-tenant HMAC keys (ADR-0005): explicit keys, the derived keys of single-VM installs
(ADR-0032 amendment 2026-10-19) and the development fallback."""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from umai_decision_engine.l0.hashing import TenantKeyError
from umai_decision_engine.settings import Settings

MASTER = base64.b64encode(bytes(range(32))).decode()


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_derived_keys_are_stable_distinct_and_32_bytes() -> None:
    settings = _settings(env="production", tenant_hmac_master_key=MASTER)
    acme = settings.tenant_key("acme")
    assert len(acme) == 32
    assert acme == _settings(env="production", tenant_hmac_master_key=MASTER).tenant_key("acme")
    assert acme != settings.tenant_key("globex")


def test_an_explicit_key_wins_over_the_master_key() -> None:
    explicit = base64.b64encode(b"k" * 32).decode()
    settings = _settings(
        env="production", tenant_hmac_master_key=MASTER, tenant_hmac_keys={"console": explicit}
    )
    assert settings.tenant_key("console") == b"k" * 32  # existing hashes keep their key
    assert settings.tenant_key("acme") != b"k" * 32


def test_a_short_master_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        _settings(env="production", tenant_hmac_master_key=base64.b64encode(b"short").decode())


def test_without_any_key_production_refuses_the_tenant() -> None:
    with pytest.raises(TenantKeyError):
        _settings(env="production").tenant_key("acme")
