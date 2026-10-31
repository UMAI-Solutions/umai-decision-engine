"""Data exports (docs/contracts/data-export.md, version in ``CONTRACT_VERSION``; ADR-0047): the
decisions CSV, the usage CSV and the organization archive, all made by the policy service. Ids,
types, codes, counts, configuration and account data only: no request content (ADR-0005), no
secret, no secret's hash."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from umai_decision_engine.contracts.common import ContractModel
from umai_decision_engine.contracts.events import Environment, Meter, Source

CONTRACT_VERSION = "0.1"

# Row caps (ADR-0047 D3): above them the export is refused with 422 ``export_too_large`` before
# anything is sent; a file is never cut short.
DECISIONS_CSV_MAX_ROWS = 100_000
USAGE_CSV_MAX_ROWS = 100_000
# Decision events and L1b outcomes together in one organization archive (ADR-0047 D4).
ARCHIVE_MAX_RECORDS = 250_000

DECISIONS_CSV_COLUMNS = (
    "occurred_at",
    "event_id",
    "request_id",
    "gateway_call_id",
    "source",
    "environment",
    "application_id",
    "key_id",
    "phase",
    "action",
    "severity",
    "decided_by",
    "degraded",
    "floor",
    "would_have",
    "guardrail",
    "guardrail_version",
    "guardrail_mode",
    "entities",
    "tags",
    "policies",
    "review",
    "review_reasons",
    "errors",
    "latency_ms",
    "latency_l0_ms",
    "latency_l1b_ms",
    "subject_role",
    "subject_department",
    "destination_app",
    "destination_class",
    "agent_tool",
    "agent_server",
)

USAGE_CSV_COLUMNS = (
    "date",
    "meter",
    "quantity",
    "records",
    "source",
    "environment",
    "application_id",
    "key_id",
    "provider",
    "model",
)


class UsageLine(ContractModel):
    """The usage ledger per UTC day and dimension: one line of the usage CSV and of
    ``records/usage_daily.jsonl`` in the archive."""

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    meter: Meter
    quantity: int = Field(ge=0)
    records: int = Field(ge=1)  # ledger records summed into the line
    source: Source
    environment: Environment | None = None
    application_id: str | None = None
    key_id: str | None = None
    provider: str | None = None
    model: str | None = None


class ArchiveFile(ContractModel):
    path: str
    records: int | None = Field(None, ge=0)  # lines of a ``.jsonl`` file, entries of a list


class ArchiveManifest(ContractModel):
    """``manifest.json``, the first file of an organization archive."""

    format: Literal["umai-organization-export"] = "umai-organization-export"
    contract_version: Literal["0.1"] = CONTRACT_VERSION
    organization: str  # the organization's id
    region: str
    generated_at: datetime
    generated_by: dict[str, str]  # {"type": "person", "sub": …} or {"type": "operator"}
    engine_version: str
    retention_days: dict[str, int]  # records kept at generation time, per kind
    files: list[ArchiveFile]
    not_included: list[str]
