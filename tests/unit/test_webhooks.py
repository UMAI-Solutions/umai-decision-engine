"""Webhooks without the policy service (ADR-0048): derived secrets and signatures checked with the
contract's own verification code, URL encryption, the SSRF guard (URL rules, address policy, the
pinned transport) and one attempt's outcomes. No network: every resolver and receiver is fake."""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from umai_decision_engine.contracts.webhooks import WebhookEvent
from umai_decision_engine.webhooks.guard import (
    BlockedAddress,
    GuardedBackend,
    GuardedTransport,
    InvalidUrl,
    address_allowed,
    check_url,
    resolve_public,
)
from umai_decision_engine.webhooks.keys import (
    UnreadableUrl,
    UrlCipher,
    signature_header,
    signing_secret,
)
from umai_decision_engine.webhooks.sender import WebhookSender, masked
from umai_decision_engine.webhooks.slack import slack_message

DOC = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "webhooks.md"
MASTER = bytes(range(32))
TENANT, ENDPOINT = uuid.uuid4(), uuid.uuid4()


def documented_verify():
    """The receiver-side ``verify`` exactly as the contract shows it."""

    (code,) = re.findall(r"```python\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    namespace: dict = {}
    exec(code, namespace)  # synthetic: our own documentation
    return namespace["verify"]


# ------------------------------------------------------------------ secrets and signatures


def test_secrets_are_derived_per_endpoint_and_version() -> None:
    first = signing_secret(MASTER, TENANT, ENDPOINT, 1)
    assert first.startswith("whsec_") and len(first) > 40
    assert signing_secret(MASTER, TENANT, ENDPOINT, 1) == first  # nothing to store
    assert signing_secret(MASTER, TENANT, ENDPOINT, 2) != first
    assert signing_secret(MASTER, TENANT, uuid.uuid4(), 1) != first
    assert signing_secret(bytes(32), TENANT, ENDPOINT, 1) != first


def test_the_documented_code_verifies_our_signature() -> None:
    verify = documented_verify()
    secret = signing_secret(MASTER, TENANT, ENDPOINT, 1)
    body = b'{"event_id":"evt_x","type":"webhook.test"}'
    now = int(time.time())
    header = signature_header([secret], body, now)
    assert re.fullmatch(r"t=\d+,v1=[0-9a-f]{64}", header)
    assert verify(secret, body, header)
    assert not verify(secret, body + b" ", header)  # the raw bytes are signed
    assert not verify(signing_secret(MASTER, TENANT, ENDPOINT, 2), body, header)
    assert not verify(secret, body, signature_header([secret], body, now - 600))  # too old


def test_during_a_rotation_both_secrets_verify() -> None:
    verify = documented_verify()
    old, new = (signing_secret(MASTER, TENANT, ENDPOINT, v) for v in (1, 2))
    body = b"{}"
    header = signature_header([new, old], body, int(time.time()))
    assert header.count("v1=") == 2
    assert verify(new, body, header) and verify(old, body, header)


def test_urls_are_sealed_for_one_endpoint() -> None:
    cipher = UrlCipher(MASTER)
    url = "https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXXXXX"
    sealed = cipher.seal(url, TENANT, ENDPOINT)
    assert "hooks.slack.com" not in sealed and sealed.startswith("v1:")
    assert cipher.open(sealed, TENANT, ENDPOINT) == url
    with pytest.raises(UnreadableUrl):
        cipher.open(sealed, TENANT, uuid.uuid4())  # copied to another row
    with pytest.raises(UnreadableUrl):
        UrlCipher(bytes(32)).open(sealed, TENANT, ENDPOINT)  # another master key


def test_masked_urls_hide_the_path() -> None:
    assert masked("https://hooks.slack.com/services/T000/B000/abcdWXYZ") == (
        "https://hooks.slack.com/…WXYZ"
    )
    assert masked("https://hooks.example.com:8443/in?token=s3cret") == (
        "https://hooks.example.com:8443/…cret"
    )
    assert masked("https://hooks.example.com/") == "https://hooks.example.com/…"


# ------------------------------------------------------------------ URL rules and addresses


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/in",  # https only
        "https://user:pw@hooks.example.com/in",
        "https://hooks.example.com/in#frag",
        "https://hooks.example.com:22/in",  # well-known port other than 443
        "https://localhost/in",
        "https://metadata.internal/in",
        "https://printer.local/in",
        "https://intranet/in",  # no dot: a local name
        "https://10.0.0.5/in",
        "https://127.0.0.1/in",
        "https://169.254.169.254/latest/meta-data",
        "https://168.63.129.16/machine",  # Azure platform address
        "https://100.64.0.1/in",  # carrier-grade NAT
        "https://0.0.0.0/in",
        "https://224.0.0.1/in",
        "https://[::1]/in",
        "https://[::ffff:10.0.0.5]/in",  # IPv4-mapped
        "https://[fd00:ec2::254]/in",
        "https://[64:ff9b::a00:5]/in",  # NAT64 of 10.0.0.5
        "https://hooks.example.com/in two",
    ],
)
def test_urls_that_can_never_be_a_receiver(url: str) -> None:
    with pytest.raises(InvalidUrl):
        check_url(url, "generic")


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.com/umai",
        "https://hooks.example.com:8443/umai?token=abc",
        "https://93.184.216.34/in",
        "https://[2606:2800:220:1:248:1893:25c8:1946]/in",
    ],
)
def test_public_urls_are_accepted(url: str) -> None:
    assert check_url(url, "generic") == url


def test_slack_endpoints_take_slack_incoming_webhooks_only() -> None:
    check_url("https://hooks.slack.com/services/T000/B000/XXXX", "slack")
    for url in (
        "https://hooks.example.com/services/T000/B000/XXXX",
        "https://hooks.slack.com/triggers/T000/123/abc",  # Workflow Builder: another format
        "https://hooks.slack.com:8443/services/T000/B000/XXXX",
    ):
        with pytest.raises(InvalidUrl):
            check_url(url, "slack")


def test_address_policy() -> None:
    from ipaddress import ip_address

    assert address_allowed(ip_address("93.184.216.34"))
    assert address_allowed(ip_address("2606:2800:220:1:248:1893:25c8:1946"))
    for refused in ("10.1.2.3", "172.16.0.1", "192.168.1.1", "fe80::1", "fc00::1", "ff02::1"):
        assert not address_allowed(ip_address(refused)), refused


def resolver_for(answers: dict[str, list[str]]):
    async def resolve(host: str, port: int) -> list[str]:
        if host not in answers:
            raise OSError("NXDOMAIN")
        return answers[host]

    return resolve


@pytest.mark.asyncio
async def test_every_answer_must_be_public() -> None:
    resolve = resolver_for(
        {
            "ok.example.com": ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"],
            "mixed.example.com": ["93.184.216.34", "10.0.0.5"],  # round robin into the VNet
            "rebind.example.com": ["169.254.169.254"],
        }
    )
    assert await resolve_public("ok.example.com", 443, resolve, timeout_s=1) == [
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    ]
    for host in ("mixed.example.com", "rebind.example.com"):
        with pytest.raises(BlockedAddress):
            await resolve_public(host, 443, resolve, timeout_s=1)
    with pytest.raises(OSError):
        await resolve_public("missing.example.com", 443, resolve, timeout_s=1)


@pytest.mark.asyncio
async def test_the_backend_refuses_before_connecting() -> None:
    backend = GuardedBackend(resolver_for({"evil.example.com": ["127.0.0.1"]}), timeout_s=1)
    with pytest.raises(BlockedAddress):
        await backend.connect_tcp("evil.example.com", 443)
    with pytest.raises(BlockedAddress):
        await backend.connect_unix_socket("/var/run/docker.sock")


@pytest.mark.asyncio
async def test_the_real_transport_goes_through_the_guard() -> None:
    """httpx's own transport with the guarded pool: the request never leaves the process."""

    transport = GuardedTransport(
        resolver_for({"internal.example.com": ["10.0.0.5"]}), connect_timeout_s=1
    )
    sender = WebhookSender(transport, connect_timeout_s=1, timeout_s=2)
    try:
        attempt = await sender.post("https://internal.example.com/hook", b"{}", {})
    finally:
        await sender.aclose()
    assert attempt.outcome == "failed" and attempt.error == "blocked_address"
    assert attempt.status_code is None


# ------------------------------------------------------------------ one attempt


async def _attempt(handler) -> object:
    sender = WebhookSender(httpx.MockTransport(handler), connect_timeout_s=1, timeout_s=2)
    try:
        return await sender.post("https://hooks.example.com/in", b"{}", {"x-test": "1"})
    finally:
        await sender.aclose()


@pytest.mark.asyncio
async def test_attempt_outcomes() -> None:
    seen: list[httpx.Request] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    attempt = await _attempt(ok)
    assert (attempt.outcome, attempt.status_code, attempt.error) == ("delivered", 204, None)
    assert seen[0].headers["user-agent"] == "UMAI-Webhooks/0.1"

    redirected = await _attempt(
        lambda r: httpx.Response(302, headers={"location": "https://169.254.169.254/"})
    )
    assert (redirected.outcome, redirected.status_code, redirected.error) == (
        "failed",
        302,
        "redirect",
    )
    failed = await _attempt(lambda r: httpx.Response(503, text="a body we never read"))
    assert (failed.status_code, failed.error) == (503, "http_status")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    assert (await _attempt(slow)).error == "timeout"

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert (await _attempt(refused)).error == "connection"


# ------------------------------------------------------------------ Slack format


def _event(type_: str, data: dict) -> WebhookEvent:
    return WebhookEvent(
        event_id=f"evt_{uuid.uuid4().hex}",
        type=type_,
        occurred_at=datetime(2026, 10, 30, 12, 5, tzinfo=UTC),
        organization_id="acme-security",
        data=data,
        links={"console": "https://console.example.com/detections"},
    )


def test_slack_message_for_a_blocked_window() -> None:
    message = slack_message(
        _event(
            "decision.blocked",
            {
                "window": {"from": "2026-10-30T12:00:00Z", "to": "2026-10-30T12:05:00Z"},
                "blocked": 12,
                "by_source": {"gateway": 12},
                "by_phase": {"PRE_LLM": 12},
                "applications": [{"application_id": "support<bot>", "blocked": 12}],
                "top_entities": [{"type": "TR_TCKN", "decisions": 7}],
                "top_policies": [],
                "sample_event_ids": [],
                "scanned": 12,
                "truncated": False,
            },
        )
    )
    assert message["text"] == "UMAI blocked 12 requests in acme-security (12:00–12:05 UTC)"
    section = message["blocks"][0]["text"]["text"]
    assert "support&lt;bot&gt; (12)" in section and "TR_TCKN (7)" in section
    link = message["blocks"][1]["elements"][0]["text"]
    assert link.startswith("<https://console.example.com/detections|Open Detections>")
    assert all(block["type"] != "actions" for block in message["blocks"])  # links, no buttons


def test_slack_messages_for_every_type() -> None:
    samples = {
        "quota.threshold": {
            "environment": "live",
            "threshold_percent": 100,
            "limit": 10000,
            "used": 10000,
            "month": "2026-10",
            "resets_at": "2026-11-01T00:00:00Z",
            "edition": "community",
        },
        "billing.payment_failed": {
            "amount": "1499.00",
            "currency": "TRY",
            "edition": "pro",
            "subscription_status": "past_due",
            "grace_until": "2026-11-13T12:00:00Z",
        },
        "billing.payment_succeeded": {"amount": "1499.00", "currency": "TRY"},
        "billing.subscription_changed": {
            "change": "plan_changed",
            "edition": "pro",
            "effective": "now",
        },
        "webhook.test": {"endpoint_id": str(ENDPOINT)},
    }
    heads = {}
    for type_, data in samples.items():
        message = slack_message(_event(type_, data))
        assert message["text"] and message["blocks"][0]["type"] == "section"
        heads[type_] = message["text"]
    assert heads["quota.threshold"] == "acme-security has used 100 % of its monthly requests"
    assert heads["billing.payment_failed"] == "A payment failed for acme-security: 1499.00 TRY"
    assert heads["billing.subscription_changed"] == "Plan changed for acme-security"
