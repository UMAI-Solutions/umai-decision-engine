"""The iyzico client's pure parts (ADR-0044; docs/reference/iyzico-subscriptions.md): request
signing, the webhook signature, parsing subscription details. Synthetic keys only."""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime
from decimal import Decimal

from umai_decision_engine.billing.iyzico import (
    authorization,
    parse_subscription,
    signature,
    verify_webhook,
    webhook_signature,
)

SECRET = "sandbox-secret"  # synthetic


def test_the_request_signature_is_hmac_over_random_key_path_and_body() -> None:
    body = '{"locale":"tr"}'
    expected = hmac.new(
        SECRET.encode(),
        b"rnd123/v2/subscription/checkoutform/initialize" + body.encode(),
        hashlib.sha256,
    ).hexdigest()
    assert signature(SECRET, "rnd123", "/v2/subscription/checkoutform/initialize", body) == expected
    header = authorization("sandbox-key", SECRET, "rnd123", "/v2/subscription/x", "")
    assert header.startswith("IYZWSv2 ")
    decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
    key, rnd, sign = decoded.split("&")
    assert (key, rnd) == ("apiKey:sandbox-key", "randomKey:rnd123")
    assert sign == "signature:" + signature(SECRET, "rnd123", "/v2/subscription/x", "")


def test_the_webhook_signature_covers_merchant_secret_event_and_references() -> None:
    payload = {
        "iyziEventType": "subscription.order.failure",
        "subscriptionReferenceCode": "sub-1",
        "orderReferenceCode": "ord-1",
        "customerReferenceCode": "cus-1",
        "iyziReferenceCode": "iyzi-1",
    }
    message = "777" + SECRET + "subscription.order.failure" + "sub-1" + "ord-1" + "cus-1"
    expected = hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
    assert webhook_signature("777", SECRET, payload) == expected
    assert verify_webhook("777", SECRET, payload, expected.upper())  # hex case does not matter
    assert not verify_webhook("777", SECRET, {**payload, "orderReferenceCode": "ord-2"}, expected)
    assert not verify_webhook("778", SECRET, payload, expected)
    assert not verify_webhook("777", SECRET, payload, "")


def test_a_subscription_detail_gives_status_orders_and_the_paid_period() -> None:
    start = int(datetime(2026, 10, 30, tzinfo=UTC).timestamp() * 1000)
    end = int(datetime(2026, 11, 29, tzinfo=UTC).timestamp() * 1000)
    later = int(datetime(2026, 12, 29, tzinfo=UTC).timestamp() * 1000)
    parsed = parse_subscription(
        {
            "referenceCode": "sub-2",
            "parentReferenceCode": "sub-1",
            "subscriptionStatus": "UNPAID",
            "startDate": start,
            "orders": [
                {
                    "referenceCode": "o1",
                    "orderStatus": "SUCCESS",
                    "price": 1499.0,
                    "currencyCode": "TRY",
                    "startPeriod": start,
                    "endPeriod": end,
                },
                {
                    "referenceCode": "o2",
                    "orderStatus": "FAILED",
                    "price": 1499.0,
                    "startPeriod": end,
                    "endPeriod": later,
                    "paymentAttempts": [
                        {"paymentStatus": "FAILED", "errorMessage": "Yetersiz bakiye"}
                    ],
                },
            ],
        }
    )
    assert (parsed.reference, parsed.parent_reference, parsed.status) == (
        "sub-2",
        "sub-1",
        "UNPAID",
    )
    assert parsed.paid_until == datetime(2026, 11, 29, tzinfo=UTC)
    assert parsed.last_order.reference == "o2" and parsed.last_order.error == "Yetersiz bakiye"
    assert parsed.successful_orders[0].price == Decimal("1499.0")
    first = parse_subscription({"referenceCode": "sub-9", "startDate": "2026-10-30 12:00:00"})
    assert first.parent_reference == "sub-9"  # its own parent when it has none
    assert first.start == datetime(2026, 10, 30, 12, tzinfo=UTC)
    assert first.paid_until is None and first.last_order is None
