"""Gateways: ``<gateways_dir>/<tenant_id>.yaml``, validated, hot-reloaded (ADR-0038).

A gateway is tenant configuration, not policy (ADR-0034 §6): a provider connection, a guardrail
and the call settings. A file that fails validation keeps the tenant's last good gateways and is
reported by location, never silently half-loaded. Provider base URLs must match the deployment's
allow-list (SSRF, contract gateway-api, Gateways).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from umai_decision_engine.contracts.gateway import TENANT_FILE, Gateway
from umai_decision_engine.gatewayfiles import AllowList, validate_gateway_file

log = logging.getLogger("umai.gateway.gateways")

_TENANT = re.compile(TENANT_FILE)


@dataclass
class GatewayState:
    by_tenant: dict[str, dict[str, Gateway]] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (location, problem)


class GatewayTable:
    def __init__(self, root: Path, allowlist: AllowList) -> None:
        self._root = root
        self._allowlist = allowlist
        self._state = GatewayState()
        self._fingerprint: str | None = None

    # ----------------------------------------------------------------- reads

    def get(self, tenant_id: str, gateway_id: str) -> Gateway | None:
        """The enabled gateway ``gateway_id`` of the tenant, or ``None``."""

        gateway = self._state.by_tenant.get(tenant_id, {}).get(gateway_id)
        return gateway if gateway is not None and gateway.enabled else None

    def gateways(self, tenant_id: str) -> list[Gateway]:
        return [g for g in self._state.by_tenant.get(tenant_id, {}).values() if g.enabled]

    @property
    def errors(self) -> list[tuple[str, str]]:
        return list(self._state.errors)

    # ----------------------------------------------------------------- loading

    def _files(self) -> list[Path]:
        if not self._root.is_dir():
            return []
        return sorted(p for p in self._root.iterdir() if p.suffix in (".yaml", ".yml"))

    def _fingerprint_now(self, files: list[Path]) -> str:
        digest = hashlib.sha256()
        for path in files:
            stat = path.stat()
            digest.update(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size};".encode())
        return digest.hexdigest()

    def reload(self, *, force: bool = False) -> bool:
        files = self._files()
        fingerprint = self._fingerprint_now(files)
        if not force and fingerprint == self._fingerprint:
            return False
        state = GatewayState()
        for path in files:
            tenant = path.stem
            if not _TENANT.match(tenant):
                state.errors.append((path.name, "file name is not a tenant id"))
                continue
            try:
                gateways = self._load_file(path)
            except (ValueError, ValidationError, yaml.YAMLError, OSError) as exc:
                problem = _problem(exc)
                state.errors.append((path.name, problem))
                log.warning("gateways.invalid file=%s problem=%s", path.name, problem)
                previous = self._state.by_tenant.get(tenant)
                if previous is not None:
                    state.by_tenant[tenant] = previous  # last known good
                continue
            state.by_tenant[tenant] = gateways
        self._state = state
        self._fingerprint = fingerprint
        log.info(
            "gateways.loaded tenants=%d gateways=%d errors=%d",
            len(state.by_tenant),
            sum(len(r) for r in state.by_tenant.values()),
            len(state.errors),
        )
        return True

    def _load_file(self, path: Path) -> dict[str, Gateway]:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        parsed = validate_gateway_file(data, self._allowlist)
        return {g.gateway_id: g for g in parsed.gateways}

    async def poll(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await asyncio.to_thread(self.reload)
            except Exception:  # keep polling; the last good state stays active
                log.exception("gateways.reload_failed")


def _problem(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first.get("loc", ())) or "gateways"
        # Operator configuration, not caller content: the message helps fix the file.
        message = str(first.get("msg", first.get("type", "invalid")))
        return f"{location}: {message}"[:300]
    return str(exc)[:300]
