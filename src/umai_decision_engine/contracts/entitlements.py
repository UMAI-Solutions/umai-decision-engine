"""Entitlements contract (docs/contracts/entitlements.md, ADR-0043): the plans file, a tenant's
limits as engines receive them in its key-registry snapshot, and the policy service's quota
read. No limit value lives in code: plans come from the deployment's plans file."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from umai_decision_engine.contracts.common import ContractModel
from umai_decision_engine.contracts.organizations import Edition

CONTRACT_VERSION = "0.3"  # 0.2: edition pro, billing (ADR-0044); 0.3: authoring limits

QuotaEnvironment = Literal["live", "test"]


class RequestLimits(ContractModel):
    """What engines enforce, sent in the snapshot. Null means no limit: counted but never
    refused, or for the size the deployment's."""

    monthly_requests: int | None = Field(None, ge=0)
    monthly_test_requests: int | None = Field(None, ge=0)
    max_input_chars: int | None = Field(None, ge=1_000)

    def monthly(self, environment: QuotaEnvironment) -> int | None:
        return self.monthly_test_requests if environment == "test" else self.monthly_requests


class Limits(RequestLimits):
    """A plan's limits. The authoring limits (0.3, ADR-0056 §9) are the policy service's alone
    and never reach an engine's snapshot."""

    max_guardrails: int | None = Field(None, ge=1)
    max_decision_policies: int | None = Field(None, ge=0)

    def request_limits(self) -> dict:
        return self.model_dump(include=set(RequestLimits.model_fields))

    def overridden(self, overrides: dict) -> Limits:
        """These limits with the fields an operator set for one tenant (null: no limit)."""

        return self.model_validate({**self.model_dump(), **overrides})


class PlanBilling(ContractModel):
    """What a paid plan costs (entitlements 0.2, ADR-0044): the amount charged per interval, VAT
    included. iyzico cannot change a plan's price: a new price is a new provider plan."""

    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: Literal["TRY", "USD", "EUR"] = "TRY"
    interval: Literal["MONTHLY"] = "MONTHLY"


class Plan(Limits):
    """A plan in the plans file: its limits and, for a paid plan, its price."""

    billing: PlanBilling | None = None

    def limits(self) -> Limits:
        return Limits.model_validate(self.model_dump(exclude={"billing"}))


class PlansFile(ContractModel):
    """``UMAI_DE_PLANS_FILE``: limits per edition; an edition it does not name has none."""

    contract_version: Literal["0.1", "0.2", "0.3"] = CONTRACT_VERSION
    plans: dict[Edition, Plan]


class TenantEntitlements(RequestLimits):
    """The ``entitlements`` block of a key-registry snapshot (credentials 0.7)."""

    edition: Edition


class RequestsUsed(ContractModel):
    requests: int = Field(ge=0)
    test_requests: int = Field(ge=0)
    guardrails: int | None = Field(None, ge=0)  # 0.3: what max_guardrails counts


class EntitlementsView(ContractModel):
    """``GET /v1/entitlements``: the calling tenant's plan and this month's requests."""

    contract_version: Literal["0.1", "0.2", "0.3"] = CONTRACT_VERSION
    tenant_id: str
    edition: Edition
    limits: Limits
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    used: RequestsUsed
    resets_at: datetime


class PlanUpdate(ContractModel):
    """``PUT /v1/tenants/{id}/plan`` (operator). A field present in ``overrides`` replaces the
    plan's value; an absent one keeps it."""

    edition: Edition
    overrides: Limits = Field(default_factory=Limits)

    def override_values(self) -> dict:
        return self.overrides.model_dump(include=self.overrides.model_fields_set)
