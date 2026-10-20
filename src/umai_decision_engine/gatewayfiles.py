"""Gateway files, shared by the gateway (reads) and the policy service (writes).

One YAML file per tenant, ``<gateways_dir>/<tenant_id>.yaml`` (contract gateway-api 0.9,
ADR-0038). Kept outside ``gateway/`` so the policy service can validate and write gateways
without importing the raw-content zone (ADR-0035, code layout).
"""

from __future__ import annotations

import fnmatch
import ipaddress
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from umai_decision_engine.contracts.gateway import TENANT_FILE, GatewayFile

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


def validate_gateway_file(data: Any, allowlist: AllowList) -> GatewayFile:
    """Schema, fallback rules and upstream allow-list (raises ValueError / ValidationError)."""

    parsed = GatewayFile.model_validate(data or {})
    for gateway in parsed.gateways:
        if not allowlist.permits(gateway.provider.base_url or ""):
            raise ValueError(
                f"gateway {gateway.gateway_id}: provider base_url is not on the gateway allow-list"
            )
    return parsed


def gateway_file_path(root: Path, tenant_id: str) -> Path:
    if not _TENANT.match(tenant_id):
        raise ValueError(f"{tenant_id!r} cannot name a gateway file")
    return root / f"{tenant_id}.yaml"


def read_gateway_file(root: Path, tenant_id: str) -> dict[str, Any]:
    path = gateway_file_path(root, tenant_id)
    if not path.is_file():
        return {"gateways": []}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {"gateways": []}


def write_gateway_file(root: Path, tenant_id: str, gateways: GatewayFile) -> Path:
    """Replace a tenant's file atomically (the gateway never reads a half-written file)."""

    path = gateway_file_path(root, tenant_id)
    root.mkdir(parents=True, exist_ok=True)
    document = gateways.model_dump(mode="json", exclude_none=True)
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
