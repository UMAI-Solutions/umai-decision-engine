"""Slack incoming-webhook messages for webhook events (ADR-0048 §5, contract webhooks "Slack
delivery", reference slack-incoming-webhooks): a ``text`` summary (notifications, screen readers)
and mrkdwn ``blocks`` with a console link. Ids, counts and amounts only, like the generic body;
every interpolated value is escaped for ``& < >``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from umai_decision_engine.contracts.webhooks import WebhookEvent

_LINK_LABELS = {
    "decision.blocked": "Open Detections",
    "quota.threshold": "Open Usage",
    "billing.payment_failed": "Open Billing",
    "billing.payment_succeeded": "Open Billing",
    "billing.subscription_changed": "Open Billing",
    "webhook.test": "Open Webhooks",
}
_CHANGES = {
    "started": "Subscription started",
    "plan_changed": "Plan changed",
    "canceled": "Subscription cancelled",
    "grace_ended": "Payment overdue: the grace period ended",
    "ended": "Subscription ended: Community limits apply",
}


def _esc(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _n(value: int) -> str:
    return f"{value:,}"


def _hm(value: Any) -> str:
    when = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return when.strftime("%H:%M")


def _day(value: Any) -> str:
    when = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return when.strftime("%d %b %Y")


def _counts(items: list[dict], key: str, count: str) -> str:
    return ", ".join(f"{_esc(i.get(key) or 'API calls')} ({_n(i[count])})" for i in items)


def _summary(event: WebhookEvent) -> tuple[str, list[str]]:
    """The headline (``text``) and the detail lines of one event."""

    org = _esc(event.organization_id)
    d = event.data
    if event.type == "decision.blocked":
        window = d["window"]
        head = (
            f"UMAI blocked {_n(d['blocked'])} request{'s' if d['blocked'] != 1 else ''} in {org} "
            f"({_hm(window['from'])}–{_hm(window['to'])} UTC)"
        )
        lines = []
        if d.get("applications"):
            lines.append(
                "*Applications:* " + _counts(d["applications"], "application_id", "blocked")
            )
        if d.get("top_entities"):
            lines.append("*Found:* " + _counts(d["top_entities"], "type", "decisions"))
        if d.get("top_policies"):
            lines.append("*Policies:* " + _counts(d["top_policies"], "id", "decisions"))
        return head, lines
    if event.type == "quota.threshold":
        env = "test-key requests" if d["environment"] == "test" else "requests"
        head = f"{org} has used {d['threshold_percent']} % of its monthly {env}"
        lines = [
            f"{_n(d['used'])} of {_n(d['limit'])} in {_esc(d['month'])}; "
            f"the count resets on {_day(d['resets_at'])}."
        ]
        if d["threshold_percent"] >= 100:
            lines.append("Further requests are refused until then, or until the plan changes.")
        return head, lines
    if event.type in ("billing.payment_succeeded", "billing.payment_failed"):
        amount = (
            f"{_esc(d['amount'])} {_esc(d.get('currency') or '')}".strip()
            if d.get("amount")
            else ""
        )
        if event.type == "billing.payment_succeeded":
            head = f"Payment received for {org}" + (f": {amount}" if amount else "")
            return head, []
        head = f"A payment failed for {org}" + (f": {amount}" if amount else "")
        lines = []
        if d.get("grace_until"):
            lines.append(
                f"The paid plan stays until {_day(d['grace_until'])}; then Community limits apply."
            )
        return head, lines
    if event.type == "billing.subscription_changed":
        head = f"{_CHANGES[d['change']]} for {org}"
        lines = []
        if d.get("edition"):
            when = {"now": " (now)", "next_period": " (from the next period)"}.get(
                d.get("effective") or "", ""
            )
            lines.append(f"*Plan:* {_esc(str(d['edition']).capitalize())}{when}")
        return head, lines
    return f"Test message from UMAI for {org}: this Slack webhook works.", []


def slack_message(event: WebhookEvent) -> dict[str, Any]:
    head, lines = _summary(event)
    text = "\n".join([f"*{head}*", *lines])
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}}
    ]
    if event.links.console:
        label = _LINK_LABELS.get(event.type, "Open the UMAI console")
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"<{_esc(event.links.console)}|{label}> · UMAI"}
                ],
            }
        )
    return {"text": head, "blocks": blocks}
