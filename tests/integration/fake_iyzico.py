"""A fake iyzico Subscription API for tests (shapes from docs/reference/iyzico-subscriptions.md).
It checks every request's IYZWSv2 signature like the real one, keeps subscriptions in memory, and
lets a test play the buyer (``pay``), the renewal run (``renew``) and the notifier (``webhook``).
Synthetic data only."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from umai_decision_engine.billing.iyzico import signature, webhook_signature

API_KEY = "sandbox-test-api-key"  # synthetic
SECRET_KEY = "sandbox-test-secret-key"  # synthetic
MERCHANT_ID = "999999"


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


class FakeIyzico:
    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime(2026, 10, 30, 12, tzinfo=UTC)
        self.products: dict[str, dict] = {}
        self.plans: dict[str, dict] = {}
        self.forms: dict[str, dict] = {}
        self.subscriptions: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.retries: list[str] = []
        self.fail_next: str | None = None  # an errorCode the next call answers with

    # ------------------------------------------------------------------ test helpers

    def add_plan(self, price: str = "1499.00", currency: str = "TRY") -> str:
        ref = f"plan-{uuid.uuid4().hex[:8]}"
        self.plans[ref] = {
            "referenceCode": ref,
            "price": float(price),
            "currencyCode": currency,
            "paymentInterval": "MONTHLY",
            "paymentIntervalCount": 1,
        }
        return ref

    def pay(self, token: str, *, status: str = "ACTIVE") -> str:
        """The buyer completes the hosted form: a subscription with its first order paid."""

        form = self.forms[token]
        ref = f"sub-{uuid.uuid4().hex[:8]}"
        plan = self.plans[form["plan"]]
        sub = {
            "referenceCode": ref,
            "parentReferenceCode": ref,
            "customerReferenceCode": f"cus-{form['customer']['email']}",
            "pricingPlanReferenceCode": form["plan"],
            "subscriptionStatus": status,
            "startDate": _ms(self.now),
            "orders": [],
        }
        if status == "ACTIVE":
            sub["orders"].append(self._order(plan, self.now, "SUCCESS"))
        self.subscriptions[ref] = sub
        form["subscription"] = ref
        return ref

    def renew(self, ref: str, *, success: bool, error: str = "Yetersiz bakiye") -> str:
        """The next period's charge; returns the order reference."""

        sub = self.subscriptions[ref]
        plan = self.plans[sub["pricingPlanReferenceCode"]]
        last_end = max(o["endPeriod"] for o in sub["orders"]) if sub["orders"] else _ms(self.now)
        start = datetime.fromtimestamp(last_end / 1000, tz=UTC)
        order = self._order(plan, start, "SUCCESS" if success else "FAILED", error)
        sub["orders"].append(order)
        if not success:
            sub["subscriptionStatus"] = "UNPAID"
        return order["referenceCode"]

    def settle_retry(self, ref: str, order_ref: str) -> None:
        """A retried order is paid."""

        sub = self.subscriptions[ref]
        for order in sub["orders"]:
            if order["referenceCode"] == order_ref:
                order["orderStatus"] = "SUCCESS"
                order["paymentAttempts"].append({"paymentStatus": "SUCCESS", "paymentId": "p1"})
        sub["subscriptionStatus"] = "ACTIVE"

    def webhook(self, ref: str, order_ref: str, *, success: bool, merchant_id: str = MERCHANT_ID):
        sub = self.subscriptions[ref]
        payload = {
            "orderReferenceCode": order_ref,
            "customerReferenceCode": sub["customerReferenceCode"],
            "subscriptionReferenceCode": ref,
            "iyziReferenceCode": f"iyzi-{uuid.uuid4().hex[:10]}",
            "iyziEventType": "subscription.order.success"
            if success
            else "subscription.order.failure",
            "iyziEventTime": _ms(self.now),
        }
        return payload, webhook_signature(merchant_id, SECRET_KEY, payload)

    def _order(self, plan: dict, start: datetime, status: str, error: str | None = None) -> dict:
        end = start + timedelta(days=30)
        attempt = {"paymentStatus": status}
        if error and status == "FAILED":
            attempt["errorMessage"] = error
        return {
            "referenceCode": f"ord-{uuid.uuid4().hex[:8]}",
            "orderStatus": status,
            "price": plan["price"],
            "currencyCode": plan["currencyCode"],
            "startPeriod": _ms(start),
            "endPeriod": _ms(end),
            "paymentAttempts": [attempt],
        }

    # ------------------------------------------------------------------ the HTTP surface

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = request.content.decode() if request.content else ""
        self.calls.append((request.method, path))
        if not self._signed(request, path, body):
            return httpx.Response(
                401, json={"status": "failure", "errorCode": "8", "errorMessage": "not verified"}
            )
        if self.fail_next:
            code, self.fail_next = self.fail_next, None
            return httpx.Response(
                200, json={"status": "failure", "errorCode": code, "errorMessage": "refused"}
            )
        payload = json.loads(body) if body else {}
        parts = path.strip("/").split("/")[2:]  # after v2/subscription
        return self._route(request.method, parts, payload)

    def _signed(self, request: httpx.Request, path: str, body: str) -> bool:
        header = request.headers.get("authorization", "")
        rnd = request.headers.get("x-iyzi-rnd", "")
        if not header.startswith("IYZWSv2 ") or not rnd:
            return False
        decoded = base64.b64decode(header[len("IYZWSv2 ") :]).decode()
        fields = dict(part.split(":", 1) for part in decoded.split("&"))
        return fields.get("apiKey") == API_KEY and fields.get("signature") == signature(
            SECRET_KEY, rnd, path, body
        )

    def _ok(self, data: Any = None, **top: Any) -> httpx.Response:
        body = {"status": "success", "systemTime": _ms(self.now), **top}
        if data is not None:
            body["data"] = data
        return httpx.Response(200, json=body)

    def _route(self, method: str, parts: list[str], payload: dict) -> httpx.Response:
        if method == "POST" and parts == ["products"]:
            ref = f"prod-{uuid.uuid4().hex[:8]}"
            self.products[ref] = {"referenceCode": ref, "name": payload["name"]}
            return self._ok(self.products[ref])
        if (
            method == "POST"
            and len(parts) == 3
            and parts[0] == "products"
            and parts[2] == "pricing-plans"
        ):
            ref = self.add_plan(f"{payload['price']:.2f}", payload["currencyCode"])
            return self._ok(self.plans[ref])
        if method == "GET" and parts[:1] == ["pricing-plans"]:
            plan = self.plans.get(parts[1])
            return self._ok(plan) if plan else self._fail("201451", "plan not found")
        if method == "POST" and parts == ["checkoutform", "initialize"]:
            token = f"tok-{uuid.uuid4().hex}"
            self.forms[token] = {
                "plan": payload["pricingPlanReferenceCode"],
                "customer": payload["customer"],
                "callback": payload["callbackUrl"],
            }
            return self._ok(
                token=token,
                checkoutFormContent='<script type="text/javascript">/* iyzico form */</script>',
                tokenExpireTime=1800,
            )
        if method == "GET" and parts[:1] == ["checkoutform"]:
            form = self.forms.get(parts[1])
            if form is None or "subscription" not in form:
                return self._fail("100001", "no result")
            sub = dict(self.subscriptions[form["subscription"]])
            sub.pop("orders", None)  # the form result has no orders (reference doc)
            return self._ok(sub)
        if parts[:1] == ["subscriptions"] and len(parts) >= 2:
            sub = self.subscriptions.get(parts[1])
            if sub is None:
                return self._fail("201600", "subscription not found")
            if method == "GET" and len(parts) == 2:
                return self._ok({"totalCount": 1, "currentPage": 1, "pageCount": 1, "items": [sub]})
            if method == "POST" and parts[2] == "cancel":
                sub["subscriptionStatus"] = "CANCELED"
                return self._ok()
            if method == "POST" and parts[2] == "upgrade":
                new_ref = f"sub-{uuid.uuid4().hex[:8]}"
                sub["subscriptionStatus"] = "UPGRADED"
                new = {
                    **sub,
                    "referenceCode": new_ref,
                    "pricingPlanReferenceCode": payload["newPricingPlanReferenceCode"],
                    "subscriptionStatus": "ACTIVE",
                    "orders": list(sub["orders"]),
                }
                if payload["upgradePeriod"] == "NOW":
                    new["orders"].append(
                        self._order(
                            self.plans[payload["newPricingPlanReferenceCode"]], self.now, "SUCCESS"
                        )
                    )
                self.subscriptions[new_ref] = new
                self.last_upgrade = payload
                return self._ok({"referenceCode": new_ref})
        if method == "POST" and parts == ["operation", "retry"]:
            self.retries.append(payload["referenceCode"])
            return self._ok()
        return httpx.Response(404, json={"status": 404, "error": "Not Found"})

    def _fail(self, code: str, message: str) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "failure", "errorCode": code, "errorMessage": message}
        )


def money(value: str) -> Decimal:
    return Decimal(value)
