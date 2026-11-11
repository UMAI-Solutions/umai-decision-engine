"""Browser-extension contract (docs/contracts/browser-extension.md, ADR-0058): the policy
service's browser settings and browser connections. A connection is an API key of the
credentials contract with ``client: browser``; its secret appears once, in the answer that made
it, never in these models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from umai_decision_engine.contracts.common import ContractModel
from umai_decision_engine.contracts.gateway import APPLICATION_ID

CONTRACT_VERSION = "0.1"
MAX_CONNECTIONS_PER_PERSON = 10  # active ones, per person and organization (ADR-0058 D2)
DEFAULT_DEVICE_LABEL = "Browser"


class BrowserSettingsUpdate(ContractModel):
    """``PUT /v1/browser/settings``."""

    application_id: str | None = Field(..., pattern=APPLICATION_ID)


class ConnectionCreate(ContractModel):
    """``POST /v1/browser/connections``."""

    device_label: str = Field(DEFAULT_DEVICE_LABEL, min_length=1, max_length=120)


class ConnectionPerson(ContractModel):
    """The connection's person as the tenant store knows them now; ``email`` and ``name`` are
    ``None`` for a person no longer in the store."""

    user_id: str
    email: str | None = None
    name: str | None = None


class BrowserConnection(ContractModel):
    key_id: str
    tenant_id: str
    user: ConnectionPerson
    device_label: str | None = None
    application_id: str | None = None
    created_at: datetime
    revoked_at: datetime | None = None
    status: Literal["active", "revoked"]
