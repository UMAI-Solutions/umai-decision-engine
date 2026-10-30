"""The billing service (ADR-0044): checkout, plan change, cancel, webhooks, reconciliation and
the subscription state machine (contract billing 0.1, "State machine").

Our subscription row decides the tenant's edition (D1). iyzico's callback, webhooks and statuses
only make us re-read the subscription and decide; a tenant's edition changes through
``TenantRepository.set_plan`` and a republished snapshot (ADR-0043), never by the provider.
Provider calls run outside database transactions; one lock per tenant serializes the state
machine within this process (the policy service runs once per stack)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from umai_decision_engine.billing.iyzico import (
    PLACEHOLDER_IDENTITY_NUMBER,
    IyzicoClient,
    IyzicoError,
    ProviderSubscription,
    verify_webhook,
)
from umai_decision_engine.contracts.billing import (
    BillingAccount,
    BillingView,
    CheckoutResult,
    CheckoutStarted,
    PlanOffer,
    SubscriptionView,
)
from umai_decision_engine.db.models import (
    BillingAccountRow,
    BillingCheckoutRow,
    BillingEventRow,
    BillingLinkRow,
    SubscriptionRow,
    Tenant,
)
from umai_decision_engine.db.repositories import TenantInfo, TenantRepository, tenant_info
from umai_decision_engine.db.session import global_session, tenant_session

log = logging.getLogger("umai.billing")

PROVIDER = "iyzico"
FREE_EDITION = "community"
LIVE = ("pending", "active", "past_due", "canceled")  # a subscription that is not over
PAID = ("active", "past_due", "canceled")  # the paid edition applies


class BillingError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Offer:
    edition: str
    reference: str | None
    available: bool


def _account_view(row: BillingAccountRow | None) -> BillingAccount | None:
    if row is None:
        return None
    return BillingAccount(
        company_name=row.company_name,
        tax_number=row.tax_number,
        tax_office=row.tax_office,
        contact_name=row.contact_name,
        contact_surname=row.contact_surname,
        email=row.email,
        phone=row.phone,
        address=row.address,
        city=row.city,
        country=row.country,
        zip_code=row.zip_code,
    )


def _subscription_view(row: SubscriptionRow | None) -> SubscriptionView | None:
    if row is None:
        return None
    return SubscriptionView(
        status=row.status,  # type: ignore[arg-type]
        edition=row.edition,  # type: ignore[arg-type]
        started_at=row.started_at,
        current_period_end=row.current_period_end,
        cancel_at=row.cancel_at,
        grace_until=row.grace_until,
        next_retry_at=row.next_retry_at,
        last_payment_error=row.last_payment_error,
    )


class BillingService:
    def __init__(
        self,
        db: AsyncEngine,
        tenants: TenantRepository,
        keys: Any,  # control_plane.keys.KeyService: plans and snapshot republishing
        *,
        client: IyzicoClient | None,
        merchant_id: str | None,
        secret_key: str | None,
        return_url: str | None,
        grace_days: int,
        retry_days: list[int],
        plan_refs: dict[str, str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.db = db
        self.tenants = tenants
        self.keys = keys
        self.client = client
        self.merchant_id = merchant_id
        self.secret_key = secret_key
        self.return_url = return_url
        self.grace = timedelta(days=grace_days)
        self.retry_days = sorted(retry_days)
        self.plan_refs = plan_refs
        self.clock = clock
        self._offers: dict[str, Offer] = {}
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}

    @property
    def provider(self) -> str:
        return PROVIDER if self.client is not None else "none"

    def _lock(self, tenant: TenantInfo) -> asyncio.Lock:
        return self._locks.setdefault(tenant.id, asyncio.Lock())

    # ------------------------------------------------------------------ plans on offer

    async def verify_plans(self) -> dict[str, Offer]:
        """Offer a paid plan only when the provider's plan charges what the plans file says
        (contract billing 0.1, ``available``): a price in two places must never drift."""

        offers: dict[str, Offer] = {}
        for edition, plan in self.keys.plans.paid().items():
            reference = self.plan_refs.get(edition)
            available = False
            if self.client is not None and reference:
                try:
                    provider = await self.client.pricing_plan(reference)
                except IyzicoError as exc:
                    log.error("billing.plan_unreadable edition=%s code=%s", edition, exc.code)
                else:
                    wanted = plan.billing
                    available = (
                        provider.price == wanted.price
                        and provider.currency == wanted.currency
                        and provider.interval == wanted.interval
                        and provider.interval_count == 1
                    )
                    if not available:
                        log.error(
                            "billing.plan_mismatch edition=%s reference=%s", edition, reference
                        )
            offers[edition] = Offer(edition, reference, available)
        self._offers = offers
        log.info(
            "billing.plans provider=%s offered=%s",
            self.provider,
            sorted(e for e, o in offers.items() if o.available),
        )
        return offers

    def offers(self) -> list[PlanOffer]:
        result = []
        paid = self.keys.plans.paid().items()
        for edition, plan in sorted(paid, key=lambda item: (item[1].billing.price, item[0])):
            offer = self._offers.get(edition)
            result.append(
                PlanOffer(
                    edition=edition,  # type: ignore[arg-type]
                    price=plan.billing.price,
                    currency=plan.billing.currency,
                    interval=plan.billing.interval,
                    limits=plan.limits(),
                    available=bool(offer and offer.available),
                )
            )
        return result

    def _offer(self, edition: str) -> Offer:
        offer = self._offers.get(edition)
        if self.client is None:
            raise BillingError(503, "billing_unavailable", "this deployment does not sell plans")
        if offer is None or not offer.available or not offer.reference:
            raise BillingError(422, "plan_not_available", f"{edition} cannot be bought here")
        return offer

    # ------------------------------------------------------------------ reads

    async def _load(self, tenant: TenantInfo):
        async with tenant_session(self.db, tenant.id) as session:
            account = await session.scalar(
                select(BillingAccountRow).where(BillingAccountRow.tenant_id == tenant.id)
            )
            subscription = await session.scalar(
                select(SubscriptionRow).where(SubscriptionRow.tenant_id == tenant.id)
            )
        return account, subscription

    async def view(self, tenant: TenantInfo, *, can_manage: bool) -> BillingView:
        tenant = await self.tenants.require(tenant.slug)  # the edition as it stands now
        account, subscription = await self._load(tenant)
        return BillingView(
            tenant_id=tenant.slug,
            edition=tenant.edition,  # type: ignore[arg-type]
            provider=self.provider,  # type: ignore[arg-type]
            can_manage=can_manage,
            subscription=_subscription_view(subscription),
            account=_account_view(account),
            plans=self.offers(),
        )

    # ------------------------------------------------------------------ billing details

    async def save_account(self, tenant: TenantInfo, account: BillingAccount, actor: str) -> None:
        async with tenant_session(self.db, tenant.id) as session:
            row = await session.scalar(
                select(BillingAccountRow).where(BillingAccountRow.tenant_id == tenant.id)
            )
            if row is None:
                row = BillingAccountRow(tenant_id=tenant.id)
                session.add(row)
            for field, value in account.model_dump().items():
                setattr(row, field, value)
            row.updated_at = self.clock()
            row.updated_by = actor
            session.add(BillingEventRow(tenant_id=tenant.id, kind="account_saved", actor=actor))
        log.info("billing.account_saved tenant=%s actor=%s", tenant.slug, actor)

    # ------------------------------------------------------------------ checkout

    async def start_checkout(self, tenant: TenantInfo, edition: str, actor: str) -> CheckoutStarted:
        offer = self._offer(edition)
        if not self.return_url:
            raise BillingError(503, "billing_unavailable", "no return address is configured")
        account, subscription = await self._load(tenant)
        if account is None:
            raise BillingError(409, "billing_account_required", "add billing details first")
        if subscription is not None and subscription.status in LIVE:
            raise BillingError(409, "already_subscribed", "change the plan instead")
        checkout_id = f"co-{uuid.uuid4().hex[:20]}"
        callback = f"{self.return_url}?org={tenant.slug}&checkout={checkout_id}"
        address = {
            "address": account.address,
            "contactName": f"{account.contact_name} {account.contact_surname}",
            "city": account.city,
            "country": account.country,
            **({"zipCode": account.zip_code} if account.zip_code else {}),
        }
        customer = {
            "name": account.contact_name,
            "surname": account.contact_surname,
            "email": account.email,
            "gsmNumber": account.phone,
            "identityNumber": PLACEHOLDER_IDENTITY_NUMBER,
            "billingAddress": address,
            "shippingAddress": address,
        }
        try:
            form = await self.client.start_checkout(  # type: ignore[union-attr]
                callback_url=callback,
                pricing_plan_reference=offer.reference,  # type: ignore[arg-type]
                customer=customer,
                conversation_id=checkout_id,
            )
        except IyzicoError as exc:
            log.warning("billing.checkout_failed tenant=%s code=%s", tenant.slug, exc.code)
            raise BillingError(502, "provider_error", "the payment provider refused") from exc
        expires_at = self.clock() + timedelta(seconds=form.expires_in_s)
        async with tenant_session(self.db, tenant.id) as session:
            session.add(
                BillingCheckoutRow(
                    tenant_id=tenant.id,
                    checkout_id=checkout_id,
                    token=form.token,
                    edition=edition,
                    pricing_plan_reference=offer.reference,
                    status="open",
                    created_by=actor,
                    expires_at=expires_at,
                )
            )
            session.add(
                BillingEventRow(
                    tenant_id=tenant.id,
                    kind="checkout_started",
                    actor=actor,
                    detail={"checkout_id": checkout_id, "edition": edition},
                )
            )
        log.info("billing.checkout_started tenant=%s edition=%s", tenant.slug, edition)
        return CheckoutStarted(
            checkout_id=checkout_id, form_content=form.content, expires_at=expires_at
        )

    async def complete_checkout(
        self, tenant: TenantInfo, checkout_id: str, token: str
    ) -> CheckoutResult:
        """The buyer came back: read the form's result from iyzico by token (the callback proves
        nothing, D2). Completing twice answers the same result."""

        if self.client is None:
            raise BillingError(503, "billing_unavailable", "this deployment does not sell plans")
        async with self._lock(tenant):
            async with tenant_session(self.db, tenant.id) as session:
                checkout = await session.scalar(
                    select(BillingCheckoutRow).where(
                        BillingCheckoutRow.tenant_id == tenant.id,
                        BillingCheckoutRow.checkout_id == checkout_id,
                    )
                )
            if checkout is None:
                raise BillingError(404, "unknown_checkout", "no such checkout")
            if checkout.token != token:
                raise BillingError(409, "token_mismatch", "the token belongs to another checkout")
            if checkout.status != "open":
                return CheckoutResult(
                    tenant_id=tenant.slug,
                    checkout_id=checkout_id,
                    status=checkout.status,  # type: ignore[arg-type]
                    edition=(await self.tenants.require(tenant.slug)).edition,  # type: ignore[arg-type]
                )
            try:
                result = await self.client.checkout_result(token)
            except IyzicoError as exc:
                log.warning("billing.checkout_unreadable tenant=%s code=%s", tenant.slug, exc.code)
                raise BillingError(
                    502, "provider_error", "the payment provider did not answer"
                ) from exc
            status = {"ACTIVE": "active", "PENDING": "pending"}.get(result.status, "failed")
            now = self.clock()
            async with tenant_session(self.db, tenant.id) as session:
                checkout.status = status
                checkout.completed_at = now
                await session.merge(checkout)
                if status != "failed":
                    existing = await session.scalar(
                        select(SubscriptionRow).where(SubscriptionRow.tenant_id == tenant.id)
                    )
                    row = existing or SubscriptionRow(tenant_id=tenant.id)
                    row.provider = PROVIDER
                    row.parent_reference = result.parent_reference
                    row.reference = result.reference
                    row.customer_reference = result.customer_reference
                    row.pricing_plan_reference = checkout.pricing_plan_reference
                    row.edition = checkout.edition
                    row.pending_edition = None
                    row.status = status
                    row.provider_status = result.status
                    row.started_at = result.start or now
                    row.current_period_end = None
                    row.cancel_at = row.grace_until = row.next_retry_at = None
                    row.retry_count = 0
                    row.failed_order_reference = row.last_payment_error = None
                    row.created_at = row.created_at or now
                    row.updated_at = now
                    if existing is None:
                        session.add(row)
                session.add(
                    BillingEventRow(
                        tenant_id=tenant.id,
                        kind="checkout_completed",
                        subscription_reference=result.reference or None,
                        detail={"checkout_id": checkout_id, "status": status},
                    )
                )
            if status != "failed":
                await self._link(tenant, result.parent_reference, result.reference)
                await self._refresh(tenant)  # the detail has the orders the form result lacks
            log.info("billing.checkout_completed tenant=%s status=%s", tenant.slug, status)
            return CheckoutResult(
                tenant_id=tenant.slug,
                checkout_id=checkout_id,
                status=status,  # type: ignore[arg-type]
                edition=(await self.tenants.require(tenant.slug)).edition,  # type: ignore[arg-type]
            )

    async def _link(self, tenant: TenantInfo, *references: str) -> None:
        """Map provider references to the tenant (global table, D4): a webhook names no tenant."""

        for reference in {r for r in references if r}:
            try:
                async with global_session(self.db) as session:
                    session.add(
                        BillingLinkRow(provider=PROVIDER, reference=reference, tenant_id=tenant.id)
                    )
            except IntegrityError:
                pass  # already linked

    # ------------------------------------------------------------------ plan change and cancel

    async def change_plan(self, tenant: TenantInfo, edition: str, actor: str) -> None:
        offer = self._offer(edition)
        async with self._lock(tenant):
            _, row = await self._load(tenant)
            if row is None or row.status != "active":
                raise BillingError(409, "no_active_subscription", "start a plan first")
            if row.edition == edition and row.pending_edition is None:
                return
            current = self.keys.plans.plan(row.edition)
            target = self.keys.plans.plan(edition)
            now_ = bool(
                current is None
                or current.billing is None
                or target.billing.price >= current.billing.price
            )
            try:
                reference = await self.client.upgrade(  # type: ignore[union-attr]
                    row.reference,
                    offer.reference,
                    now=now_,  # type: ignore[arg-type]
                )
            except IyzicoError as exc:
                log.warning("billing.change_failed tenant=%s code=%s", tenant.slug, exc.code)
                raise BillingError(502, "provider_error", "the payment provider refused") from exc
            row.reference = reference
            row.pricing_plan_reference = offer.reference  # type: ignore[assignment]
            if now_:
                row.edition, row.pending_edition = edition, None
            else:
                row.pending_edition = edition  # from the next period (D7)
            row.updated_at = self.clock()
            await self._save(
                tenant,
                row,
                BillingEventRow(
                    tenant_id=tenant.id,
                    kind="plan_changed",
                    actor=actor,
                    subscription_reference=reference,
                    detail={"edition": edition, "when": "now" if now_ else "next_period"},
                ),
            )
            await self._link(tenant, reference)
            await self._apply_edition(tenant, row)
        log.info("billing.plan_changed tenant=%s edition=%s now=%s", tenant.slug, edition, now_)

    async def cancel(self, tenant: TenantInfo, actor: str) -> None:
        if self.client is None:
            raise BillingError(503, "billing_unavailable", "this deployment does not sell plans")
        async with self._lock(tenant):
            _, row = await self._load(tenant)
            if row is None or row.status not in ("active", "past_due", "pending"):
                raise BillingError(409, "no_active_subscription", "nothing to cancel")
            try:
                await self.client.cancel(row.reference)
            except IyzicoError as exc:
                log.warning("billing.cancel_failed tenant=%s code=%s", tenant.slug, exc.code)
                raise BillingError(502, "provider_error", "the payment provider refused") from exc
            now = self.clock()
            row.status = "canceled"
            row.cancel_at = row.current_period_end if row.current_period_end else now
            row.next_retry_at = None
            row.updated_at = now
            await self._save(
                tenant,
                row,
                BillingEventRow(
                    tenant_id=tenant.id,
                    kind="canceled",
                    actor=actor,
                    subscription_reference=row.reference,
                ),
            )
            await self._settle(tenant, row)
        log.info("billing.canceled tenant=%s", tenant.slug)

    # ------------------------------------------------------------------ webhook

    async def handle_webhook(self, payload: dict[str, Any], signature: str) -> str:
        """Verify, deduplicate, re-read (D5). Returns what happened, for the log line."""

        if not (self.merchant_id and self.secret_key) or not verify_webhook(
            self.merchant_id, self.secret_key, payload, signature
        ):
            raise BillingError(401, "invalid_signature", "the notification signature is wrong")
        reference = str(payload.get("subscriptionReferenceCode") or "")
        references = [reference] if reference else []
        tenant = await self._tenant_of(references)
        if tenant is None:
            log.warning("billing.webhook_unknown_subscription")
            return "unknown"
        event_id = str(payload.get("iyziReferenceCode") or "") or None
        try:
            async with tenant_session(self.db, tenant.id) as session:
                session.add(
                    BillingEventRow(
                        tenant_id=tenant.id,
                        kind="webhook",
                        provider_event_id=event_id,
                        subscription_reference=references[0] if references else None,
                        order_reference=payload.get("orderReferenceCode"),
                        detail={"type": payload.get("iyziEventType")},
                    )
                )
        except IntegrityError:
            return "duplicate"
        await self.refresh(tenant)
        return "applied"

    async def _tenant_of(self, references: list[str]) -> TenantInfo | None:
        if not references:
            return None
        async with global_session(self.db) as session:
            tenant_id = await session.scalar(
                select(BillingLinkRow.tenant_id).where(
                    BillingLinkRow.provider == PROVIDER, BillingLinkRow.reference.in_(references)
                )
            )
            row = await session.get(Tenant, tenant_id) if tenant_id else None
        return tenant_info(row) if row is not None else None

    # ------------------------------------------------------------------ state machine

    async def refresh(self, tenant: TenantInfo) -> None:
        async with self._lock(tenant):
            await self._refresh(tenant)

    async def _refresh(self, tenant: TenantInfo) -> None:
        """Re-read the provider's subscription and the clock, then decide (contract billing,
        "State machine")."""

        _, row = await self._load(tenant)
        if row is None or row.status == "ended" or self.client is None:
            return
        provider: ProviderSubscription | None
        try:
            provider = await self.client.subscription(row.reference)
        except IyzicoError as exc:
            log.warning("billing.refresh_unreadable tenant=%s code=%s", tenant.slug, exc.code)
            provider = None
        events: list[BillingEventRow] = []
        now = self.clock()
        if provider is not None:
            row.provider_status = provider.status
            row.customer_reference = provider.customer_reference or row.customer_reference
            self._payments(tenant, row, provider, now, events)
            if provider.status in ("CANCELED", "EXPIRED") and row.status in ("active", "pending"):
                row.status = "canceled"  # cancelled outside UMAI: paid until the period ends
                row.cancel_at = row.current_period_end or now
                events.append(BillingEventRow(tenant_id=tenant.id, kind="canceled_by_provider"))
        await self._dunning(tenant, row, now, events)
        if row.pending_edition and row.current_period_end and row.status == "active":
            # a downgrade takes effect once a period at the new price is paid (D7)
            last = provider.last_order if provider is not None else None
            if last is not None and last.status == "SUCCESS" and last.price is not None:
                target = self.keys.plans.plan(row.pending_edition)
                if target is not None and target.billing and last.price == target.billing.price:
                    row.edition, row.pending_edition = row.pending_edition, None
        row.updated_at = now
        await self._save(tenant, row, *events)
        await self._settle(tenant, row)

    def _payments(
        self,
        tenant: TenantInfo,
        row: SubscriptionRow,
        provider: ProviderSubscription,
        now: datetime,
        events: list[BillingEventRow],
    ) -> None:
        paid_until = provider.paid_until
        if paid_until and (row.current_period_end is None or paid_until > row.current_period_end):
            row.current_period_end = paid_until
            for order in provider.successful_orders:
                events.append(
                    BillingEventRow(
                        tenant_id=tenant.id,
                        kind="payment_succeeded",
                        provider_event_id=f"order:{order.reference}:success",
                        subscription_reference=row.reference,
                        order_reference=order.reference,
                        amount=str(order.price) if order.price is not None else None,
                        currency=order.currency,
                    )
                )
            if row.status in ("pending", "past_due"):
                row.status = "active"
                row.grace_until = row.next_retry_at = None
                row.retry_count = 0
                row.failed_order_reference = row.last_payment_error = None
        last = provider.last_order
        if (
            last is not None
            and last.status == "FAILED"
            and row.status in ("active", "pending")
            and last.reference != row.failed_order_reference
        ):
            row.status = "past_due"
            row.failed_order_reference = last.reference
            row.last_payment_error = (last.error or "payment failed")[:200]
            row.grace_until = now + self.grace
            row.retry_count = 0
            row.next_retry_at = (
                now + timedelta(days=self.retry_days[0]) if self.retry_days else None
            )
            events.append(
                BillingEventRow(
                    tenant_id=tenant.id,
                    kind="payment_failed",
                    provider_event_id=f"order:{last.reference}:failed",
                    subscription_reference=row.reference,
                    order_reference=last.reference,
                    amount=str(last.price) if last.price is not None else None,
                    currency=last.currency,
                    detail={"error": row.last_payment_error},
                )
            )

    async def _dunning(
        self, tenant: TenantInfo, row: SubscriptionRow, now: datetime, events: list[BillingEventRow]
    ) -> None:
        """Retries on the schedule, then the end of grace (D6). A retry is never resent blindly:
        its outcome arrives as a payment (webhook or the next read)."""

        if row.status != "past_due":
            return
        if row.grace_until and now >= row.grace_until:
            try:
                await self.client.cancel(row.reference)  # type: ignore[union-attr]
            except IyzicoError as exc:
                log.warning("billing.grace_cancel_failed tenant=%s code=%s", tenant.slug, exc.code)
            row.status = "canceled"
            row.cancel_at = now
            row.next_retry_at = None
            events.append(
                BillingEventRow(
                    tenant_id=tenant.id, kind="grace_ended", subscription_reference=row.reference
                )
            )
            return
        if row.next_retry_at and now >= row.next_retry_at and row.failed_order_reference:
            try:
                await self.client.retry(row.failed_order_reference)  # type: ignore[union-attr]
                outcome = "requested"
            except IyzicoError as exc:
                outcome = f"refused:{exc.code}"
            row.retry_count += 1
            failed_at = (row.grace_until or now) - self.grace
            later = (
                self.retry_days[row.retry_count :] if row.retry_count < len(self.retry_days) else []
            )
            row.next_retry_at = failed_at + timedelta(days=later[0]) if later else None
            events.append(
                BillingEventRow(
                    tenant_id=tenant.id,
                    kind="retry",
                    order_reference=row.failed_order_reference,
                    detail={"attempt": row.retry_count, "outcome": outcome},
                )
            )

    async def _save(
        self, tenant: TenantInfo, row: SubscriptionRow, *events: BillingEventRow
    ) -> None:
        async with tenant_session(self.db, tenant.id) as session:
            await session.merge(row)
        for event in events:  # each alone: a repeated provider event id is skipped, not fatal
            try:
                async with tenant_session(self.db, tenant.id) as session:
                    session.add(event)
            except IntegrityError:
                pass

    async def _settle(self, tenant: TenantInfo, row: SubscriptionRow) -> None:
        """A cancellation ends when its paid period does; then the edition follows the row."""

        now = self.clock()
        if row.status == "canceled" and row.cancel_at and now >= row.cancel_at:
            row.status = "ended"
            row.updated_at = now
            await self._save(
                tenant,
                row,
                BillingEventRow(
                    tenant_id=tenant.id, kind="ended", subscription_reference=row.reference
                ),
            )
        await self._apply_edition(tenant, row)

    async def _apply_edition(self, tenant: TenantInfo, row: SubscriptionRow) -> None:
        current = await self.tenants.require(tenant.slug)
        wanted = (
            row.edition
            if row.status in PAID
            else (FREE_EDITION if row.status == "ended" else current.edition)
        )
        if wanted == current.edition:
            return
        updated = await self.tenants.set_plan(
            current.slug, wanted, current.entitlement_overrides or {}
        )
        published = await self.keys.republish(updated)
        log.info(
            "billing.edition tenant=%s from=%s to=%s published=%s",
            tenant.slug,
            current.edition,
            wanted,
            published,
        )

    # ------------------------------------------------------------------ reconciliation

    async def reconcile(self) -> int:
        """Re-read every subscription that is not over (D5): a missed webhook, a retry's outcome,
        a period that ended. Returns how many were read."""

        if self.client is None:
            return 0
        count = 0
        for tenant in await self.tenants.list():
            _, row = await self._load(tenant)
            if row is None or row.status == "ended":
                continue
            try:
                await self.refresh(tenant)
                count += 1
            except Exception:  # one tenant never stops the others
                log.exception("billing.reconcile_failed tenant=%s", tenant.slug)
        return count

    async def run(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                read = await self.reconcile()
                log.info("billing.reconciled subscriptions=%d", read)
            except Exception:
                log.exception("billing reconciliation failed")
