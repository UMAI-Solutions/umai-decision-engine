"""Data exports in the policy service (ADR-0047, contract data-export 0.1): the decisions CSV, the
usage CSV and the organization archive.

Everything here is read through tenant sessions (ADR-0025 §9) and written from allow-lists: a
column or field reaches an export only when it is named below, so a secret added to a table later
is not exported by accident. No request content exists to export (ADR-0005, ADR-0041 §2)."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import tempfile
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import IO, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from umai_decision_engine import __version__
from umai_decision_engine.contracts.events import DecisionEvent
from umai_decision_engine.contracts.exports import (
    ARCHIVE_MAX_RECORDS,
    DECISIONS_CSV_COLUMNS,
    DECISIONS_CSV_MAX_ROWS,
    USAGE_CSV_COLUMNS,
    USAGE_CSV_MAX_ROWS,
    ArchiveFile,
    ArchiveManifest,
    UsageLine,
)
from umai_decision_engine.control_plane.organizations import (
    OrganizationService,
    organization_view,
)
from umai_decision_engine.control_plane.records import EventFilters, RecordsReader
from umai_decision_engine.control_plane.repository import CHANGELOG, GuardrailRepositories
from umai_decision_engine.db.models import (
    BillingAccountRow,
    BillingEventRow,
    L1bOutcomeRow,
    SubscriptionRow,
)
from umai_decision_engine.db.repositories import DraftRepository, TenantInfo
from umai_decision_engine.db.session import tenant_session

log = logging.getLogger("umai.control.exports")

# ------------------------------------------------------------------ CSV (data-export §1, §2)

# UTF-8 with a byte-order mark, so spreadsheet programs read Turkish characters as UTF-8.
BOM = "﻿"
# A cell starting with one of these is a formula to a spreadsheet: request ids and subject
# fields come from callers, so such cells are prefixed with an apostrophe (CSV injection).
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def cell(value: Any) -> str:
    if value is None:
        return ""
    if type(value) is str:  # most cells: checked first
        return "'" + value if value.startswith(_FORMULA) else value
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    text = str(value)
    return "'" + text if text.startswith(_FORMULA) else text


def csv_lines(rows: list[list[Any]]) -> str:
    """RFC 4180: comma-separated, quoted when needed, CRLF line ends."""

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in rows:
        writer.writerow([cell(v) for v in row])
    return buffer.getvalue()


def _joined(items: list[str]) -> str:
    return " ".join(items)


def decision_cells(event: DecisionEvent) -> list[Any]:
    """One decision event as the decisions CSV's columns (``DECISIONS_CSV_COLUMNS``)."""

    decision, subject = event.decision, event.subject
    destination, agent = event.destination, event.agent_action
    values = {
        "occurred_at": event.occurred_at,
        "event_id": event.event_id,
        "request_id": event.request_id,
        "gateway_call_id": event.gateway_call_id,
        "source": event.source,
        "environment": event.environment,
        "application_id": event.application_id,
        "key_id": event.key_id,
        "phase": event.phase,
        "action": decision.action,
        "severity": decision.severity,
        "decided_by": decision.decided_by,
        "degraded": decision.degraded,
        "floor": decision.floor,
        "would_have": decision.would_have,
        "guardrail": event.bundle.id,
        "guardrail_version": event.bundle.version,
        "guardrail_mode": event.bundle.mode,
        "entities": _joined([f"{e.type}:{e.count}" for e in event.entities]),
        "tags": _joined([t.code for t in event.tags]),
        "policies": _joined([f"{m.kind}:{m.id}" for m in event.matched]),
        "review": event.review,
        "review_reasons": _joined(event.review_reasons),
        "errors": _joined([f"{cell(e.layer)}:{e.type}" for e in event.errors]),
        "latency_ms": event.latency_ms.total,
        "latency_l0_ms": event.latency_ms.l0,
        "latency_l1b_ms": event.latency_ms.l1b,
        "subject_role": subject.role if subject else None,
        "subject_department": subject.department if subject else None,
        "destination_app": destination.app if destination else None,
        "destination_class": destination.class_ if destination else None,
        "agent_tool": agent.tool_name if agent else None,
        "agent_server": agent.server_name if agent else None,
    }
    return [values[c] for c in DECISIONS_CSV_COLUMNS]


def usage_cells(line: UsageLine) -> list[Any]:
    return [getattr(line, c) for c in USAGE_CSV_COLUMNS]


async def decisions_csv(
    reader: RecordsReader, tenant: TenantInfo, filters: EventFilters, *, max_rows: int
) -> AsyncIterator[bytes]:
    """The decisions CSV, newest first, streamed batch by batch; never more than ``max_rows``
    rows (the caller has counted them already, so the cap only guards a late arrival)."""

    yield (BOM + csv_lines([list(DECISIONS_CSV_COLUMNS)])).encode("utf-8")
    left = max_rows
    async for batch in reader.stream_events(tenant, filters):
        batch = batch[:left]
        yield await asyncio.to_thread(_decision_lines, batch)
        left -= len(batch)
        if left <= 0:
            return


def _decision_lines(events: list[DecisionEvent]) -> bytes:
    return csv_lines([decision_cells(e) for e in events]).encode("utf-8")


def usage_csv(lines: list[UsageLine]) -> bytes:
    rows = [list(USAGE_CSV_COLUMNS)] + [usage_cells(line) for line in lines]
    return (BOM + csv_lines(rows)).encode("utf-8")


# ------------------------------------------------------------------ archive (data-export §3)

# Fields of the billing tables the archive carries; anything else stays out (checkout tokens,
# for one). Card data never reaches UMAI: iyzico's hosted form takes it (ADR-0044).
ACCOUNT_FIELDS = (
    "company_name",
    "tax_number",
    "tax_office",
    "contact_name",
    "contact_surname",
    "email",
    "phone",
    "address",
    "city",
    "country",
    "zip_code",
    "updated_at",
    "updated_by",
)
SUBSCRIPTION_FIELDS = (
    "provider",
    "reference",
    "parent_reference",
    "customer_reference",
    "pricing_plan_reference",
    "edition",
    "pending_edition",
    "status",
    "provider_status",
    "started_at",
    "current_period_end",
    "cancel_at",
    "grace_until",
    "next_retry_at",
    "retry_count",
    "failed_order_reference",
    "last_payment_error",
    "created_at",
    "updated_at",
)
BILLING_EVENT_FIELDS = (
    "occurred_at",
    "kind",
    "provider_event_id",
    "subscription_reference",
    "order_reference",
    "amount",
    "currency",
    "actor",
    "detail",
)

NOT_INCLUDED = [
    "Request and answer content: UMAI never records it (ADR-0005, ADR-0041).",
    "API key secrets and their hashes: a secret is shown once, at creation.",
    "Invitation link secrets and their hashes.",
    "Provider keys: they are held by the deployment, never stored per organization.",
    "Signing keys, HMAC keys and the console's assertion keys.",
    "Payment card data: UMAI never receives it (iyzico's hosted form takes it).",
    "Hosted checkout tokens.",
    "Records past their retention period: they no longer exist.",
]

# Compressed archives up to this size stay in memory; larger ones spill to a temporary file.
SPOOL_BYTES = 32 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


@dataclass
class ExportLimits:
    """Row caps of the CSV exports (data-export 0.1); a test may lower them."""

    decisions_csv: int = DECISIONS_CSV_MAX_ROWS
    usage_csv: int = USAGE_CSV_MAX_ROWS


class ExportTooLarge(Exception):
    def __init__(self, records: int, limit: int) -> None:
        super().__init__(f"more than {limit} records")
        self.records = records
        self.limit = limit


class ExportRunning(Exception):
    pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value


def _picked(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: _jsonable(getattr(row, name)) for name in fields}


def _json(data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _files(directory: Path) -> list[Path]:
    """Regular files under ``directory`` (no links, no hidden names), in a stable order."""

    root = directory.resolve()
    found = []
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        found.append(path)
    return found


class ArchiveWriter:
    """A zip archive built in a spooled temporary file; zip work runs in worker threads."""

    def __init__(self) -> None:
        # Outlives this object: read_chunks() streams it to the client, then closes it.
        self.file: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES)  # noqa: SIM115
        self.zip = zipfile.ZipFile(self.file, "w", compression=zipfile.ZIP_DEFLATED)
        self.entries: list[ArchiveFile] = []

    async def add(self, path: str, data: bytes, records: int | None = None) -> None:
        await asyncio.to_thread(self.zip.writestr, path, data)
        self.entries.append(ArchiveFile(path=path, records=records))

    async def add_lines(self, path: str, batches: AsyncIterator[list[str]]) -> int:
        """A JSON Lines file written batch by batch (each item one JSON document); returns its
        number of lines."""

        handle = await asyncio.to_thread(self.zip.open, path, "w")
        count = 0
        try:
            async for batch in batches:
                data = "".join(line + "\n" for line in batch).encode("utf-8")
                await asyncio.to_thread(handle.write, data)
                count += len(batch)
        finally:
            await asyncio.to_thread(handle.close)
        self.entries.append(ArchiveFile(path=path, records=count))
        return count

    async def finish(self) -> int:
        """Close the zip; the file is rewound for reading. Returns its size in bytes."""

        await asyncio.to_thread(self.zip.close)
        size = await asyncio.to_thread(self.file.seek, 0, 2)
        await asyncio.to_thread(self.file.seek, 0)
        return size

    def close(self) -> None:
        self.zip.close()
        self.file.close()


async def read_chunks(file: IO[bytes]) -> AsyncIterator[bytes]:
    """Stream a finished archive and close it afterwards (also when the client goes away)."""

    try:
        while chunk := await asyncio.to_thread(file.read, CHUNK_BYTES):
            yield chunk
    finally:
        file.close()


class OrganizationExporter:
    """The organization archive (ADR-0047 D4): configuration, people, keys, guardrails, records
    and billing of one tenant, as one zip of JSON files."""

    def __init__(
        self,
        db: AsyncEngine,
        records: RecordsReader,
        organizations: OrganizationService,
        keys: Any,  # KeyService (list, plans)
        guardrails: GuardrailRepositories,
        drafts: DraftRepository,
        applications: Callable[[TenantInfo], Awaitable[dict[str, Any]]],
        *,
        retention_days: dict[str, int],
        max_records: int = ARCHIVE_MAX_RECORDS,
    ) -> None:
        self._db = db
        self._records = records
        self._organizations = organizations
        self._keys = keys
        self._guardrails = guardrails
        self._drafts = drafts
        self._applications = applications
        self.retention_days = retention_days
        self.max_records = max_records
        self._running: set[str] = set()

    async def export(self, tenant: TenantInfo, actor: dict[str, str]) -> tuple[IO[bytes], int, int]:
        """The archive (rewound, for :func:`read_chunks`), its size in bytes and its number of
        records. One export per organization at a time in this process."""

        if tenant.slug in self._running:
            raise ExportRunning(tenant.slug)
        self._running.add(tenant.slug)
        try:
            records = await self._check_size(tenant)
            archive = ArchiveWriter()
            try:
                await self._write(archive, tenant, actor)
                size = await archive.finish()
            except BaseException:
                archive.close()
                raise
        finally:
            self._running.discard(tenant.slug)
        return archive.file, size, records

    async def _check_size(self, tenant: TenantInfo) -> int:
        """Decision events plus L1b outcomes, counted up to the limit; raises above it."""

        up_to = self.max_records + 1
        events = await self._records.count_events(tenant, EventFilters(), up_to=up_to)
        outcomes = await self._records.count_rows(L1bOutcomeRow, tenant, up_to=up_to)
        if events + outcomes > self.max_records:
            raise ExportTooLarge(events + outcomes, self.max_records)
        return events + outcomes

    async def _write(self, archive: ArchiveWriter, tenant: TenantInfo, actor: dict) -> None:
        generated_at = datetime.now(UTC)
        organization = {
            **organization_view(tenant),
            "terms_version": tenant.terms_version,
            "terms_accepted_at": _jsonable(tenant.terms_accepted_at),
            "limits": self._keys.plans.limits(tenant).model_dump(mode="json"),
            "limit_overrides": tenant.entitlement_overrides or {},
        }
        await archive.add("organization.json", _json(organization))

        members = await self._organizations.members(tenant)
        invitations = await self._organizations.list_invitations(tenant)
        await archive.add(
            "members.json",
            _json({"members": members, "invitations": invitations}),
            len(members),
        )

        keys = [
            {**k.model_dump(mode="json", exclude_none=True), "status": k.status}
            for k in await self._keys.list(tenant)
        ]
        await archive.add("api_keys.json", _json({"keys": keys}), len(keys))

        applications = await self._applications(tenant)
        await archive.add(
            "applications.json", _json(applications), len(applications.get("applications", []))
        )

        await self._write_guardrails(archive, tenant)

        await archive.add_lines(
            "records/decision_events.jsonl",
            self._event_batches(tenant),
        )
        await archive.add_lines(
            "records/l1b_outcomes.jsonl",
            self._outcome_batches(tenant),
        )
        lines = await self._records.usage_lines(tenant, None, None, up_to=self.max_records + 1)
        if len(lines) > self.max_records:
            raise ExportTooLarge(len(lines), self.max_records)
        await archive.add_lines(
            "records/usage_daily.jsonl", _once([line.model_dump_json() for line in lines])
        )

        await self._write_billing(archive, tenant)

        manifest = ArchiveManifest(
            organization=tenant.slug,
            region=tenant.region,
            generated_at=generated_at,
            generated_by=actor,
            engine_version=__version__,
            retention_days=self.retention_days,
            files=[*archive.entries, ArchiveFile(path="manifest.json")],
            not_included=NOT_INCLUDED,
        )
        await archive.add("manifest.json", _json(manifest.model_dump(mode="json")))

    async def _event_batches(self, tenant: TenantInfo) -> AsyncIterator[list[str]]:
        async for batch in self._records.stream_events(tenant, EventFilters()):
            yield await asyncio.to_thread(
                lambda b: [e.model_dump_json(by_alias=True, exclude_none=True) for e in b], batch
            )

    async def _outcome_batches(self, tenant: TenantInfo) -> AsyncIterator[list[str]]:
        async for batch in self._records.stream_outcomes(tenant):
            yield await asyncio.to_thread(
                lambda b: [o.model_dump_json(exclude_none=True) for o in b], batch
            )

    async def _write_guardrails(self, archive: ArchiveWriter, tenant: TenantInfo) -> None:
        repo = self._guardrails.for_tenant(tenant.slug)
        index = []
        for bundle_id in await asyncio.to_thread(repo.ids):
            versions = await asyncio.to_thread(repo.versions, bundle_id)
            draft = await self._drafts.get(tenant, bundle_id)
            index.append(
                {
                    "id": bundle_id,
                    "active_version": await asyncio.to_thread(repo.active, bundle_id),
                    "versions": versions,
                    "draft": draft is not None,
                }
            )
            for version in versions:
                directory = repo.version_dir(bundle_id, version)
                for path in await asyncio.to_thread(_files, directory):
                    relative = path.relative_to(directory).as_posix()
                    data = await asyncio.to_thread(path.read_bytes)
                    await archive.add(f"guardrails/{bundle_id}/{version}/{relative}", data)
            changelog = repo.root / bundle_id / CHANGELOG
            if await asyncio.to_thread(changelog.is_file):
                data = await asyncio.to_thread(changelog.read_bytes)
                await archive.add(
                    f"guardrails/{bundle_id}/{CHANGELOG}", data, data.count(b"\n") or None
                )
            if draft is not None:
                await archive.add(
                    f"guardrails/{bundle_id}/draft.json",
                    _json(
                        {
                            "base_version": draft.base_version,
                            "created_at": draft.created_at,
                            "updated_at": draft.updated_at,
                            "bundle": draft.bundle,
                        }
                    ),
                )
        await archive.add("guardrails/index.json", _json({"guardrails": index}), len(index))

    async def _write_billing(self, archive: ArchiveWriter, tenant: TenantInfo) -> None:
        async with tenant_session(self._db, tenant.id) as session:
            account = await session.scalar(
                select(BillingAccountRow).where(BillingAccountRow.tenant_id == tenant.id)
            )
            subscriptions = (
                await session.scalars(
                    select(SubscriptionRow)
                    .where(SubscriptionRow.tenant_id == tenant.id)
                    .order_by(SubscriptionRow.created_at)
                )
            ).all()
            events = (
                await session.scalars(
                    select(BillingEventRow)
                    .where(BillingEventRow.tenant_id == tenant.id)
                    .order_by(BillingEventRow.occurred_at, BillingEventRow.id)
                )
            ).all()
            billing = {
                "account": _picked(account, ACCOUNT_FIELDS) if account else None,
                "subscriptions": [_picked(s, SUBSCRIPTION_FIELDS) for s in subscriptions],
                "events": [_picked(e, BILLING_EVENT_FIELDS) for e in events],
            }
        await archive.add("billing.json", _json(billing), len(billing["events"]))


async def _once(items: list[str]) -> AsyncIterator[list[str]]:
    if items:
        yield items
