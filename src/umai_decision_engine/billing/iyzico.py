"""iyzico Subscription API client (ADR-0044; facts: docs/reference/iyzico-subscriptions.md).

Async ``httpx`` with explicit timeouts, ``IYZWSv2`` signing, and nothing retried: iyzico's calls
are not idempotent, so a caller that times out re-reads state instead of resending. Customer
details pass through to iyzico and are never logged; card data never reaches this module (the
hosted form takes it)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

log = logging.getLogger("umai.billing.iyzico")

# iyzico documents placeholder identity numbers as accepted (reference: FAQ-SUB, FAQ-PAY); UMAI
# never asks for or stores a national identity number (ADR-0044 D4).
PLACEHOLDER_IDENTITY_NUMBER = "11111111111"


class IyzicoError(Exception):
    """A failure answer, an HTTP error or an unreadable body. ``code`` is iyzico's errorCode."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(f"iyzico {code}: {message}")
        self.code = code
        self.message = message
        self.status = status


def signature(secret_key: str, random_key: str, path: str, body: str) -> str:
    """``IYZWSv2`` request signature: hex HMAC-SHA256 over ``randomKey + path + body`` (the path
    without host or query string)."""

    message = f"{random_key}{path}{body}".encode()
    return hmac.new(secret_key.encode(), message, hashlib.sha256).hexdigest()


def authorization(api_key: str, secret_key: str, random_key: str, path: str, body: str) -> str:
    sign = signature(secret_key, random_key, path, body)
    token = f"apiKey:{api_key}&randomKey:{random_key}&signature:{sign}"
    return "IYZWSv2 " + base64.b64encode(token.encode()).decode()


def webhook_signature(merchant_id: str, secret_key: str, payload: dict[str, Any]) -> str:
    """``X-IYZ-SIGNATURE-V3`` of a subscription notification (merchantId first, as iyzico's code
    samples; the prose's order is to be confirmed in the sandbox, reference doc)."""

    message = "".join(
        str(part or "")
        for part in (
            merchant_id,
            secret_key,
            payload.get("iyziEventType"),
            payload.get("subscriptionReferenceCode"),
            payload.get("orderReferenceCode"),
            payload.get("customerReferenceCode"),
        )
    )
    return hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify_webhook(merchant_id: str, secret_key: str, payload: dict[str, Any], header: str) -> bool:
    expected = webhook_signature(merchant_id, secret_key, payload)
    return hmac.compare_digest(expected, (header or "").strip().lower())


def _instant(value: Any) -> datetime | None:
    """iyzico dates: epoch milliseconds (most responses) or text (some examples)."""

    if value is None or value == "":
        return None
    if isinstance(value, int | float) or (isinstance(value, str) and value.isdigit()):
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Order:
    reference: str | None
    status: str  # WAITING | SUCCESS | FAILED
    price: Decimal | None
    currency: str | None
    start: datetime | None
    end: datetime | None
    error: str | None  # the last failed attempt's message


@dataclass(frozen=True)
class ProviderSubscription:
    """The parts of a subscription detail UMAI decides on."""

    reference: str
    parent_reference: str
    customer_reference: str | None
    pricing_plan_reference: str | None
    status: str  # ACTIVE | PENDING | UNPAID | UPGRADED | CANCELED | EXPIRED
    start: datetime | None
    end: datetime | None
    orders: tuple[Order, ...]

    @property
    def paid_until(self) -> datetime | None:
        ends = [o.end for o in self.orders if o.status == "SUCCESS" and o.end is not None]
        return max(ends) if ends else None

    @property
    def last_order(self) -> Order | None:
        dated = [o for o in self.orders if o.start is not None]
        return (
            max(dated, key=lambda o: o.start)
            if dated
            else (self.orders[-1] if self.orders else None)
        )

    @property
    def successful_orders(self) -> tuple[Order, ...]:
        return tuple(o for o in self.orders if o.status == "SUCCESS")


def parse_subscription(item: dict[str, Any]) -> ProviderSubscription:
    orders = []
    for order in item.get("orders") or []:
        attempts = order.get("paymentAttempts") or []
        failed = [a for a in attempts if a.get("paymentStatus") == "FAILED"]
        price = order.get("price")
        orders.append(
            Order(
                reference=order.get("referenceCode") or order.get("orderReferenceCode"),
                status=str(order.get("orderStatus") or "WAITING"),
                price=Decimal(str(price)) if price is not None else None,
                currency=order.get("currencyCode"),
                start=_instant(order.get("startPeriod")),
                end=_instant(order.get("endPeriod")),
                error=(failed[-1].get("errorMessage") if failed else None),
            )
        )
    reference = str(item.get("referenceCode") or "")
    return ProviderSubscription(
        reference=reference,
        parent_reference=str(item.get("parentReferenceCode") or reference),
        customer_reference=item.get("customerReferenceCode"),
        pricing_plan_reference=item.get("pricingPlanReferenceCode"),
        status=str(item.get("subscriptionStatus") or ""),
        start=_instant(item.get("startDate")),
        end=_instant(item.get("endDate")),
        orders=tuple(orders),
    )


@dataclass(frozen=True)
class CheckoutForm:
    token: str
    content: str
    expires_in_s: int


@dataclass(frozen=True)
class PricingPlan:
    reference: str
    price: Decimal
    currency: str
    interval: str
    interval_count: int


class IyzicoClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        secret_key: str,
        *,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_s),
            transport=transport,
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # No body is signed on a body-less call, as the docs' Postman script (the SDK signs
        # "null" instead; which one the server wants is checked in the sandbox).
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False) if payload else ""
        random_key = secrets.token_hex(8)
        headers = {
            "Authorization": authorization(self._api_key, self._secret_key, random_key, path, body),
            "x-iyzi-rnd": random_key,
        }
        if body:
            headers["Content-Type"] = "application/json"
        try:
            response = await self._client.request(
                method, path, content=body.encode() if body else None, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise IyzicoError("timeout", "iyzico did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise IyzicoError("unreachable", type(exc).__name__) from exc
        try:
            data = response.json()
        except ValueError:
            raise IyzicoError("invalid_response", "not JSON", response.status_code) from None
        if not isinstance(data, dict) or data.get("status") != "success":
            body_ = data if isinstance(data, dict) else {}
            raise IyzicoError(
                str(body_.get("errorCode") or response.status_code),
                str(body_.get("errorMessage") or body_.get("error") or "request failed"),
                response.status_code,
            )
        return data

    # ------------------------------------------------------------------ catalogue (CLI)

    async def create_product(self, name: str, description: str) -> str:
        data = await self._call(
            "POST", "/v2/subscription/products", {"name": name, "description": description}
        )
        return str(data["data"]["referenceCode"])

    async def create_pricing_plan(
        self, product_reference: str, name: str, price: Decimal, currency: str, interval: str
    ) -> str:
        payload = {
            "name": name,
            "price": float(price),
            "currencyCode": currency,
            "paymentInterval": interval,
            "paymentIntervalCount": 1,
            "planPaymentType": "RECURRING",
        }
        data = await self._call(
            "POST", f"/v2/subscription/products/{product_reference}/pricing-plans", payload
        )
        return str(data["data"]["referenceCode"])

    async def pricing_plan(self, reference: str) -> PricingPlan:
        data = (await self._call("GET", f"/v2/subscription/pricing-plans/{reference}"))["data"]
        return PricingPlan(
            reference=str(data.get("referenceCode") or reference),
            price=Decimal(str(data.get("price"))),
            currency=str(data.get("currencyCode")),
            interval=str(data.get("paymentInterval")),
            interval_count=int(data.get("paymentIntervalCount") or 1),
        )

    # ------------------------------------------------------------------ subscriptions

    async def start_checkout(
        self,
        *,
        callback_url: str,
        pricing_plan_reference: str,
        customer: dict[str, Any],
        conversation_id: str,
        locale: str = "tr",
    ) -> CheckoutForm:
        payload = {
            "locale": locale,
            "conversationId": conversation_id,
            "callbackUrl": callback_url,
            "pricingPlanReferenceCode": pricing_plan_reference,
            "subscriptionInitialStatus": "ACTIVE",
            "customer": customer,
        }
        data = await self._call("POST", "/v2/subscription/checkoutform/initialize", payload)
        return CheckoutForm(
            token=str(data["token"]),
            content=str(data["checkoutFormContent"]),
            expires_in_s=int(data.get("tokenExpireTime") or 1800),
        )

    async def checkout_result(self, token: str) -> ProviderSubscription:
        data = await self._call("GET", f"/v2/subscription/checkoutform/{token}")
        return parse_subscription(data.get("data") or {})

    async def subscription(self, reference: str) -> ProviderSubscription:
        data = await self._call("GET", f"/v2/subscription/subscriptions/{reference}")
        body = data.get("data") or {}
        items = body.get("items") if isinstance(body, dict) and "items" in body else [body]
        if not items:
            raise IyzicoError("not_found", f"no subscription {reference}")
        return parse_subscription(items[0])

    async def upgrade(self, reference: str, pricing_plan_reference: str, *, now: bool) -> str:
        payload = {
            "newPricingPlanReferenceCode": pricing_plan_reference,
            "upgradePeriod": "NOW" if now else "NEXT_PERIOD",
            "useTrial": False,
            "resetRecurrenceCount": True,
        }
        data = await self._call(
            "POST", f"/v2/subscription/subscriptions/{reference}/upgrade", payload
        )
        return str((data.get("data") or {}).get("referenceCode") or reference)

    async def cancel(self, reference: str) -> None:
        await self._call("POST", f"/v2/subscription/subscriptions/{reference}/cancel")

    async def retry(self, order_reference: str) -> None:
        await self._call(
            "POST", "/v2/subscription/operation/retry", {"referenceCode": order_reference}
        )
