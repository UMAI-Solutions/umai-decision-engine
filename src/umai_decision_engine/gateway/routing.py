"""Gateway routes: ``<gateway_routes_dir>/<tenant_id>.yaml``, validated, hot-reloaded.

A route is tenant configuration, not policy (ADR-0034 §6): it names the upstream, the credential
mode and the guardrail. A file that fails validation keeps the tenant's last good routes and is
reported by location, never silently half-loaded. Upstream base URLs must match the deployment's
allow-list (SSRF, contract gateway-api, Routes).
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import ipaddress
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import ValidationError

from umai_decision_engine.contracts.gateway import TENANT_FILE, Route, RouteFile

log = logging.getLogger("umai.gateway.routes")

_TENANT = re.compile(TENANT_FILE)


@dataclass(frozen=True)
class _Entry:
    scheme: str
    host: str
    port: int | None


class AllowList:
    """Entries ``scheme://host-glob[:port]``; ``https://*.openai.azure.com`` allows any port-less
    https host under that domain. An IP-literal host is allowed only when an entry names it
    exactly, so a wildcard never reaches internal addresses by accident."""

    def __init__(self, entries: list[str]) -> None:
        self._entries: list[_Entry] = []
        for raw in entries:
            parts = urlsplit(raw.strip().lower())
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise ValueError(f"invalid gateway upstream allow-list entry: {raw!r}")
            self._entries.append(_Entry(parts.scheme, parts.hostname, parts.port))

    def permits(self, url: str) -> bool:
        try:
            parts = urlsplit(url.strip())
            host, port, scheme = parts.hostname, parts.port, parts.scheme.lower()
        except ValueError:
            return False
        if not host or scheme not in ("http", "https") or parts.username or parts.password:
            return False
        host = host.lower()
        literal = _is_ip(host)
        for entry in self._entries:
            if entry.scheme != scheme or entry.port != port:
                continue
            if literal:
                if host == entry.host:
                    return True
            elif fnmatch.fnmatchcase(host, entry.host):
                return True
        return False


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


@dataclass
class RouteState:
    by_tenant: dict[str, dict[str, Route]] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (location, problem)


class RouteTable:
    def __init__(self, root: Path, allowlist: AllowList) -> None:
        self._root = root
        self._allowlist = allowlist
        self._state = RouteState()
        self._fingerprint: str | None = None

    # ----------------------------------------------------------------- reads

    def get(self, tenant_id: str, route_id: str) -> Route | None:
        route = self._state.by_tenant.get(tenant_id, {}).get(route_id)
        return route if route is not None and route.enabled else None

    def routes(self, tenant_id: str) -> list[Route]:
        return [r for r in self._state.by_tenant.get(tenant_id, {}).values() if r.enabled]

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
        state = RouteState()
        for path in files:
            tenant = path.stem
            if not _TENANT.match(tenant):
                state.errors.append((path.name, "file name is not a tenant id"))
                continue
            try:
                routes = self._load_file(path)
            except (ValueError, ValidationError, yaml.YAMLError, OSError) as exc:
                problem = _problem(exc)
                state.errors.append((path.name, problem))
                log.warning("routes.invalid file=%s problem=%s", path.name, problem)
                previous = self._state.by_tenant.get(tenant)
                if previous is not None:
                    state.by_tenant[tenant] = previous  # last known good
                continue
            state.by_tenant[tenant] = routes
        self._state = state
        self._fingerprint = fingerprint
        log.info(
            "routes.loaded tenants=%d routes=%d errors=%d",
            len(state.by_tenant),
            sum(len(r) for r in state.by_tenant.values()),
            len(state.errors),
        )
        return True

    def _load_file(self, path: Path) -> dict[str, Route]:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        parsed = RouteFile.model_validate(data)
        for route in parsed.routes:
            if not self._allowlist.permits(route.upstream.base_url):
                raise ValueError(
                    f"route {route.route_id}: upstream base_url is not on the gateway allow-list"
                )
        return {r.route_id: r for r in parsed.routes}

    async def poll(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await asyncio.to_thread(self.reload)
            except Exception:  # keep polling; the last good state stays active
                log.exception("routes.reload_failed")


def _problem(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first.get("loc", ()))
        return f"{location}: {first.get('type', 'invalid')}"[:300]
    return str(exc)[:300]
