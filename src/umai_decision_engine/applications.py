"""Applications files: one per tenant, ``<applications_dir>/<tenant_id>.yaml`` (gateway-api
0.10, ADR-0039): the tenant's applications, the AI gateway's providers and defaults.

Read by the engine API and the gateway (an application's guardrail binds its keys on every
path), written by the policy service. Kept outside ``gateway/`` and ``api/`` so all three can
share it without importing the raw-content zone (ADR-0035, code layout).
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import ipaddress
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from pydantic import ValidationError

from umai_decision_engine.contracts.gateway import TENANT_FILE, Application, ApplicationFile

log = logging.getLogger("umai.applications")

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


def validate_application_file(data: Any, allowlist: AllowList) -> ApplicationFile:
    """Schema, references, fallback rules and the provider allow-list (raises ValueError /
    ValidationError)."""

    parsed = ApplicationFile.model_validate(data or {})
    for provider in parsed.providers:
        if not allowlist.permits(provider.base_url or ""):
            raise ValueError(
                f"provider {provider.provider_id}: base_url is not on the gateway allow-list"
            )
    return parsed


def application_file_path(root: Path, tenant_id: str) -> Path:
    if not _TENANT.match(tenant_id):
        raise ValueError(f"{tenant_id!r} cannot name an applications file")
    return root / f"{tenant_id}.yaml"


def read_application_file(root: Path, tenant_id: str) -> dict[str, Any]:
    path = application_file_path(root, tenant_id)
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def write_application_file(root: Path, tenant_id: str, content: ApplicationFile) -> Path:
    """Replace a tenant's file atomically (the gateway never reads a half-written file)."""

    path = application_file_path(root, tenant_id)
    root.mkdir(parents=True, exist_ok=True)
    document = content.model_dump(mode="json", exclude_none=True)
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    handle, temporary = tempfile.mkstemp(dir=root, prefix=f".{tenant_id}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


# ------------------------------------------------------------------ the loaded files


def problem(exc: Exception) -> str:
    """Operator configuration, not caller content: the message helps fix the file."""

    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first.get("loc", ())) or "applications"
        return f"{location}: {first.get('msg', first.get('type', 'invalid'))}"[:300]
    return str(exc)[:300]


@dataclass
class _State:
    by_tenant: dict[str, ApplicationFile] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (file, problem)


class ApplicationTable:
    """The applications files, validated and hot-reloaded. A file that fails validation keeps the
    tenant's last good content and is reported by name, never half-loaded."""

    def __init__(self, root: Path, allowlist: AllowList) -> None:
        self._root = root
        self._allowlist = allowlist
        self._state = _State()
        self._fingerprint: str | None = None

    def tenant(self, tenant_id: str) -> ApplicationFile:
        """The tenant's file; an empty one (built-in OpenAI provider, no applications) if none."""

        return self._state.by_tenant.get(tenant_id) or ApplicationFile()

    def application(self, tenant_id: str, application_id: str) -> Application | None:
        """The enabled application ``application_id`` of the tenant, or ``None``."""

        app = self.tenant(tenant_id).application(application_id)
        return app if app is not None and app.enabled else None

    def applications(self, tenant_id: str) -> list[Application]:
        return [a for a in self.tenant(tenant_id).applications if a.enabled]

    @property
    def errors(self) -> list[tuple[str, str]]:
        return list(self._state.errors)

    def _files(self) -> list[Path]:
        if not self._root.is_dir():
            return []
        return sorted(p for p in self._root.iterdir() if p.suffix in (".yaml", ".yml"))

    def reload(self, *, force: bool = False) -> bool:
        files = self._files()
        digest = hashlib.sha256()
        for path in files:
            stat = path.stat()
            digest.update(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size};".encode())
        fingerprint = digest.hexdigest()
        if not force and fingerprint == self._fingerprint:
            return False
        state = _State()
        for path in files:
            tenant = path.stem
            if not _TENANT.match(tenant):
                state.errors.append((path.name, "file name is not a tenant id"))
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                state.by_tenant[tenant] = validate_application_file(data, self._allowlist)
            except (ValueError, ValidationError, yaml.YAMLError, OSError) as exc:
                reason = problem(exc)
                state.errors.append((path.name, reason))
                log.warning("applications.invalid file=%s problem=%s", path.name, reason)
                previous = self._state.by_tenant.get(tenant)
                if previous is not None:
                    state.by_tenant[tenant] = previous  # last known good
        self._state = state
        self._fingerprint = fingerprint
        log.info(
            "applications.loaded tenants=%d applications=%d errors=%d",
            len(state.by_tenant),
            sum(len(f.applications) for f in state.by_tenant.values()),
            len(state.errors),
        )
        return True

    async def poll(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await asyncio.to_thread(self.reload)
            except Exception:  # keep polling; the last good state stays active
                log.exception("applications.reload_failed")
