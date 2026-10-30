"""Billing contract (docs/contracts/billing.md, ADR-0044): what the console reads and sends for an
organization's paid plan. Card data never appears here: payment happens in iyzico's hosted form.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from umai_decision_engine.contracts.common import ContractModel
from umai_decision_engine.contracts.entitlements import Limits
from umai_decision_engine.contracts.organizations import Edition

CONTRACT_VERSION = "0.1"

# pending: confirmed checkout whose first payment is not in yet · active: paid · past_due: a
# payment failed, grace running · canceled: cancelled, paid until cancel_at · ended: no longer paid
SubscriptionStatus = Literal["pending", "active", "past_due", "canceled", "ended"]


class BillingAccount(ContractModel):
    """The organization's billing details (invoice recipient and the contact iyzico requires)."""

    company_name: str = Field(min_length=1, max_length=200)
    tax_number: str = Field(pattern=r"^\d{10,11}$")  # VKN (10), or 11 digits for sole traders
    tax_office: str = Field(min_length=1, max_length=100)
    contact_name: str = Field(min_length=1, max_length=100)
    contact_surname: str = Field(min_length=1, max_length=100)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    phone: str = Field(pattern=r"^\+90\d{10}$")  # iyzico: Turkish numbers only
    address: str = Field(min_length=1, max_length=300)
    city: str = Field(min_length=1, max_length=100)
    country: str = Field("Turkey", min_length=1, max_length=100)
    zip_code: str | None = Field(None, max_length=20)


class PlanOffer(ContractModel):
    edition: Edition
    price: Decimal
    currency: str
    interval: str
    limits: Limits
    available: bool  # the deployment has a provider plan with this price; else not for sale


class SubscriptionView(ContractModel):
    status: SubscriptionStatus
    edition: Edition
    provider: Literal["iyzico"] = "iyzico"
    started_at: datetime | None = None
    current_period_end: datetime | None = None  # paid until
    cancel_at: datetime | None = None  # canceled: the paid edition ends here
    grace_until: datetime | None = None  # past_due: Community limits after this
    next_retry_at: datetime | None = None
    last_payment_error: str | None = None  # the provider's message, e.g. insufficient funds


class BillingView(ContractModel):
    """``GET /v1/billing``."""

    contract_version: Literal["0.1"] = CONTRACT_VERSION
    tenant_id: str
    edition: Edition
    provider: Literal["iyzico", "none"]
    can_manage: bool  # the caller may buy, change, cancel and edit details
    subscription: SubscriptionView | None = None
    account: BillingAccount | None = None
    plans: list[PlanOffer] = Field(default_factory=list)


class CheckoutRequest(ContractModel):
    edition: Edition


class CheckoutStarted(ContractModel):
    """The hosted form to show: iyzico's script, rendered into ``#iyzipay-checkout-form``."""

    checkout_id: str
    form_content: str
    expires_at: datetime


class CheckoutCompletion(ContractModel):
    token: str = Field(min_length=1, max_length=512)


class CheckoutResult(ContractModel):
    tenant_id: str
    checkout_id: str
    status: Literal["active", "pending", "failed"]
    edition: Edition


class PlanChange(ContractModel):
    edition: Edition
