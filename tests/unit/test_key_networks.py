"""Key network restrictions at admission (ADR-0046): allowed networks per key, the client
address behind trusted proxies, the failed-credential throttle per source, and snapshot
compatibility. Documentation addresses only (RFC 5737, RFC 3849)."""

from __future__ import annotations

import base64
import ipaddress
import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from pydantic import ValidationError

from umai_decision_engine.contracts.credentials import (
    MAX_ALLOWED_IPS,
    KeyRegistryEntry,
    KeyRegistrySnapshot,
    NetworkListError,
    Scope,
    canonical_networks,
)
from umai_decision_engine.settings import Settings
from umai_decision_engine.tenancy.admission import Admission
from umai_decision_engine.tenancy.keys import generate_key
from umai_decision_engine.tenancy.limits import FailedAuthThrottle
from umai_decision_engine.tenancy.network import (
    client_address,
    describe_source,
    parse_address,
    parse_networks,
    source_of,
)
from umai_decision_engine.tenancy.registry import KeyRegistry
from umai_decision_engine.tenancy.snapshot import sign_snapshot, verify_snapshot

ip = ipaddress.ip_address


def _entry(generated, allowed_ips=None) -> KeyRegistryEntry:
    return KeyRegistryEntry(
        key_id=generated.key_id,
        secret_sha256=generated.secret_sha256,
        env="live",
        region="sh",
        scopes=[Scope.EVALUATE],
        status="active",
        allowed_ips=allowed_ips,
    )


def _registry(
    *entries: KeyRegistryEntry, raw: bytes | None = None, public_keys: dict | None = None
) -> KeyRegistry:
    payload = raw or (
        KeyRegistrySnapshot(
            tenant_id="tenant-a", generated_at=datetime.now(UTC), keys=list(entries)
        )
        .model_dump_json(exclude_none=True)
        .encode()
    )

    class Source:
        name = "memory"

        def fingerprint(self) -> str:
            return "x"

        def scan(self):
            return [("memory/tenant-a.json", payload)]

    registry = KeyRegistry(
        Source(), public_keys=public_keys or {}, require_signed=public_keys is not None
    )
    registry.reload(force=True)
    assert not registry.state.errors, registry.state.errors
    return registry


def _admission(registry=None, *, shared=None, throttle=None, trusted=()) -> Admission:
    return Admission(
        shared_token=shared,
        registry=registry,
        limiter=None,
        development=False,
        throttle=throttle,
        trusted_proxies=parse_networks(trusted),
    )


def _refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call()
    return exc.value


# ------------------------------------------------------------ allowed networks (contract)


def test_networks_are_canonical_cidrs() -> None:
    assert canonical_networks(
        ["203.0.113.7", "198.51.100.0/24", "2001:DB8:0:0::/48", "2001:db8::1", " 192.0.2.0/28 "]
    ) == ["203.0.113.7/32", "198.51.100.0/24", "2001:db8::/48", "2001:db8::1/128", "192.0.2.0/28"]


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (["203.0.113.5/24"], "entry 1 has host bits set: write 203.0.113.0/24"),
        (["198.51.100.0/24", "2001:db8::1/32"], "entry 2 has host bits set: write 2001:db8::/32"),
        (["not-an-address"], "entry 1 is not an IP address or CIDR network"),
        (["203.0.113.0/33"], "entry 1 is not an IP address or CIDR network"),
        (["fe80::1%eth0"], "entry 1 is not an IP address or CIDR network"),
        ([""], "entry 1 is not an IP address or CIDR network"),
        (["203.0.113.7", "203.0.113.7/32"], "entry 2 repeats 203.0.113.7/32"),
        ([f"198.51.100.{i}" for i in range(MAX_ALLOWED_IPS + 1)], "at most 50 networks"),
    ],
)
def test_wrong_networks_are_refused_naming_the_entry(values, message) -> None:
    with pytest.raises(NetworkListError) as exc:
        canonical_networks(values)
    assert message in str(exc.value)


def test_registry_entries_validate_their_networks() -> None:
    generated = generate_key("live", "sh")
    with pytest.raises(ValidationError):
        _entry(generated, ["203.0.113.5/24"])
    with pytest.raises(ValidationError):
        _entry(generated, [])  # absent means any address; an empty list is not written
    assert _entry(generated, ["2001:DB8::/32"]).allowed_ips == ["2001:db8::/32"]


# ------------------------------------------------------------ client address


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("203.0.113.7", "203.0.113.7"),
        (" 203.0.113.7:5000", "203.0.113.7"),
        ("[2001:db8::7]:443", "2001:db8::7"),
        ("2001:db8::7", "2001:db8::7"),
        ("::ffff:203.0.113.7", "203.0.113.7"),  # IPv4-mapped: compared as IPv4
        ("testclient", None),
        ("", None),
        (None, None),
        ("[2001:db8::7", None),
    ],
)
def test_parse_address(value, expected) -> None:
    parsed = parse_address(value)
    assert (str(parsed) if parsed is not None else None) == expected


PROXY = parse_networks(["192.0.2.10/32", "192.0.2.64/26"])


def test_without_trusted_proxies_the_peer_is_the_client_and_forwarded_for_is_ignored() -> None:
    assert client_address("198.51.100.4", ["203.0.113.7"], ()) == ip("198.51.100.4")
    # an untrusted peer cannot name another client either
    assert client_address("198.51.100.4", ["203.0.113.7"], PROXY) == ip("198.51.100.4")


def test_behind_trusted_proxies_the_rightmost_untrusted_hop_is_the_client() -> None:
    # The leftmost entry is whatever the client sent; only what a trusted proxy appended counts.
    assert client_address("192.0.2.10", ["198.51.100.66, 203.0.113.7"], PROXY) == ip("203.0.113.7")
    # two trusted proxies in a row, the header split over two lines
    assert client_address("192.0.2.10", ["198.51.100.66, 203.0.113.7", "192.0.2.70"], PROXY) == ip(
        "203.0.113.7"
    )
    assert client_address("192.0.2.10", ["2001:db8::9"], PROXY) == ip("2001:db8::9")
    assert client_address("192.0.2.10", [], PROXY) == ip("192.0.2.10")  # the proxy's own call
    assert client_address("192.0.2.10", ["192.0.2.65"], PROXY) == ip("192.0.2.65")  # all trusted
    assert client_address("192.0.2.10", ["198.51.100.66, junk"], PROXY) is None  # unreadable hop
    assert client_address("testclient", ["203.0.113.7"], PROXY) is None


def test_failures_are_counted_per_ipv4_address_and_per_ipv6_64() -> None:
    assert source_of(ip("203.0.113.7")) != source_of(ip("203.0.113.8"))
    assert source_of(ip("2001:db8:0:1::7")) == source_of(ip("2001:db8:0:1:ffff::9"))
    assert source_of(ip("2001:db8:0:1::7")) != source_of(ip("2001:db8:0:2::7"))
    assert describe_source(source_of(ip("2001:db8:0:1::7"))) == "2001:db8:0:1::/64"
    assert describe_source(source_of(ip("203.0.113.7"))) == "203.0.113.7"
    assert describe_source(source_of(None)) == "unknown"


def test_settings_refuse_a_wrong_trusted_proxy() -> None:
    assert Settings(trusted_proxies=["192.0.2.10", "2001:db8::/32"]).trusted_proxies == [
        "192.0.2.10/32",
        "2001:db8::/32",
    ]
    with pytest.raises(ValidationError):
        Settings(trusted_proxies=["192.0.2.10/24"])
    with pytest.raises(ValidationError):
        Settings(trusted_proxies=["caddy"])


# ------------------------------------------------------------ admission: allowed networks


def test_a_key_with_networks_is_admitted_only_from_them() -> None:
    generated = generate_key("live", "sh")
    admission = _admission(_registry(_entry(generated, ["203.0.113.0/24", "2001:db8:1::/48"])))
    header = f"Bearer {generated.presentation}"
    for allowed in ("203.0.113.7", "2001:db8:1:2::9", "::ffff:203.0.113.200"):
        principal = admission.authenticate(header, client=parse_address(allowed))
        assert principal.key_id == generated.key_id
    for refused in ("198.51.100.7", "2001:db8:2::9", None):
        exc = _refused(lambda r=refused: admission.authenticate(header, client=parse_address(r)))
        assert exc.status_code == 403
        assert exc.detail == {
            "error": "ip_not_allowed",
            "detail": "this key may not be used from this address",
        }


def test_a_key_without_networks_is_admitted_from_anywhere() -> None:
    generated = generate_key("live", "sh")
    admission = _admission(_registry(_entry(generated)))
    for client in ("198.51.100.7", "2001:db8::1", None):
        assert admission.authenticate(
            f"Bearer {generated.presentation}", client=parse_address(client)
        )


def test_a_refused_address_is_not_a_failed_credential() -> None:
    generated = generate_key("live", "sh")
    throttle = FailedAuthThrottle(2, 60, max_sources=100)
    admission = _admission(_registry(_entry(generated, ["203.0.113.0/24"])), throttle=throttle)
    elsewhere = parse_address("198.51.100.7")
    for _ in range(5):
        exc = _refused(
            lambda: admission.authenticate(f"Bearer {generated.presentation}", client=elsewhere)
        )
        assert exc.status_code == 403
    assert len(throttle) == 0


# ------------------------------------------------------------ admission: failed credentials


def test_bad_credentials_from_one_source_are_throttled_before_any_lookup() -> None:
    clock = [0.0]
    generated = generate_key("live", "sh")
    throttle = FailedAuthThrottle(3, 60, max_sources=100, clock=lambda: clock[0])
    admission = _admission(_registry(_entry(generated)), shared="shared-secret", throttle=throttle)
    attacker, neighbour = parse_address("198.51.100.7"), parse_address("198.51.100.8")
    forged = "Bearer umai_live_sh_AAAAAAAA_" + "b" * 43
    for header in (forged, "Bearer wrong-shared-token", forged):
        assert (
            _refused(lambda h=header: admission.authenticate(h, client=attacker)).status_code == 401
        )
    refused = _refused(lambda: admission.authenticate(forged, client=attacker))
    assert refused.status_code == 429
    assert refused.detail["error"] == "too_many_failed_attempts"
    assert refused.headers["Retry-After"] == "20"  # one attempt back after window / limit
    # while throttled even a valid credential from that source is refused, unlooked-at
    valid = f"Bearer {generated.presentation}"
    assert _refused(lambda: admission.authenticate(valid, client=attacker)).status_code == 429
    # another source is not affected
    assert admission.authenticate(valid, client=neighbour).key_id == generated.key_id
    # one attempt comes back after window / limit seconds, then the source is throttled again
    clock[0] += 20
    assert _refused(lambda: admission.authenticate(forged, client=attacker)).status_code == 401
    assert _refused(lambda: admission.authenticate(valid, client=attacker)).status_code == 429
    # a whole window without failures: the count is reset
    clock[0] += 60
    assert admission.authenticate(valid, client=attacker).key_id == generated.key_id
    assert len(throttle) == 0 or throttle.blocked(source_of(attacker)) is None


def test_a_success_does_not_reset_the_count() -> None:
    generated = generate_key("live", "sh")
    throttle = FailedAuthThrottle(2, 3600, max_sources=100, clock=lambda: 0.0)
    admission = _admission(_registry(_entry(generated)), throttle=throttle)
    source = parse_address("203.0.113.7")
    valid, forged = f"Bearer {generated.presentation}", "Bearer umai_live_sh_AAAAAAAA_" + "c" * 43
    _refused(lambda: admission.authenticate(forged, client=source))
    admission.authenticate(valid, client=source)
    _refused(lambda: admission.authenticate(forged, client=source))
    assert _refused(lambda: admission.authenticate(valid, client=source)).status_code == 429


def test_a_missing_credential_is_not_counted() -> None:
    throttle = FailedAuthThrottle(1, 60, max_sources=100)
    admission = _admission(shared="shared-secret", throttle=throttle)
    source = parse_address("203.0.113.7")
    for header in (None, "", "Basic abc", "Bearer "):
        assert (
            _refused(lambda h=header: admission.authenticate(h, client=source)).status_code == 401
        )
    assert len(throttle) == 0
    assert admission.authenticate("Bearer shared-secret", client=source).kind == "shared_token"


def test_one_ipv6_64_shares_one_count() -> None:
    throttle = FailedAuthThrottle(2, 60, max_sources=100)
    admission = _admission(shared="shared-secret", throttle=throttle)
    for host in ("2001:db8:0:1::1", "2001:db8:0:1::2"):
        _refused(lambda h=host: admission.authenticate("Bearer x", client=parse_address(h)))
    rotated = parse_address("2001:db8:0:1:abcd::3")
    assert _refused(lambda: admission.authenticate("Bearer x", client=rotated)).status_code == 429
    other = parse_address("2001:db8:0:2::1")
    assert admission.authenticate("Bearer shared-secret", client=other).kind == "shared_token"


def test_the_throttle_table_is_bounded_and_forgets_full_buckets() -> None:
    clock = [0.0]
    throttle = FailedAuthThrottle(5, 10, max_sources=3, clock=lambda: clock[0])
    for n in range(5):
        throttle.failed(("a", n))
    assert len(throttle) == 3 and throttle.evicted == 2  # the oldest were forgotten
    clock[0] += 10.5
    throttle.failed("fresh")
    assert len(throttle) == 1  # untouched for a window: full again, dropped
    assert throttle.evicted == 2


def test_failed_reports_the_start_of_a_lockout_once() -> None:
    throttle = FailedAuthThrottle(2, 60, max_sources=10, clock=lambda: 0.0)
    assert [throttle.failed("s") for _ in range(2)] == [False, True]
    assert throttle.blocked("s") == pytest.approx(30.0)
    assert throttle.blocked("other") is None


# ------------------------------------------------------------ snapshots


def _keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode(), base64.b64encode(public).decode()


def test_snapshot_0_5_carries_networks_signed_and_compiled() -> None:
    private, public = _keypair()
    generated = generate_key("live", "sh")
    snapshot = KeyRegistrySnapshot(
        tenant_id="tenant-a",
        generated_at=datetime.now(UTC),
        keys=[_entry(generated, ["203.0.113.0/24"])],
    )
    assert snapshot.contract_version == "0.5"
    signed = sign_snapshot(snapshot, private, "k1")
    loaded = KeyRegistrySnapshot.model_validate_json(signed.model_dump_json(exclude_none=True))
    assert verify_snapshot(loaded, {"k1": public}, require_signed=True)
    tampered = loaded.model_copy(
        update={"keys": [loaded.keys[0].model_copy(update={"allowed_ips": ["0.0.0.0/0"]})]}
    )
    with pytest.raises(Exception, match="invalid"):
        verify_snapshot(tampered, {"k1": public}, require_signed=True)
    raw = signed.model_dump_json(exclude_none=True).encode()
    key = _registry(raw=raw, public_keys={"k1": public}).lookup(generated.key_id)
    assert key is not None and key.networks == parse_networks(["203.0.113.0/24"])


def test_older_snapshots_still_load_and_verify() -> None:
    """A 0.4 payload written before credentials 0.8 has no allowed_ips: it still loads, its
    signature still verifies, and its keys accept any address."""

    private, public = _keypair()
    generated = generate_key("live", "sh")
    old = KeyRegistrySnapshot(
        contract_version="0.4",
        tenant_id="tenant-a",
        generated_at=datetime.now(UTC),
        keys=[_entry(generated)],
    )
    signed = sign_snapshot(old, private, "k1")
    raw = json.dumps(signed.model_dump(mode="json", exclude_none=True))
    assert "allowed_ips" not in raw
    loaded = KeyRegistrySnapshot.model_validate_json(raw)
    assert verify_snapshot(loaded, {"k1": public}, require_signed=True)
    registry = _registry(raw=raw.encode(), public_keys={"k1": public})
    key = registry.lookup(generated.key_id)
    assert key is not None and key.networks is None
    admission = _admission(registry)
    principal = admission.authenticate(
        f"Bearer {generated.presentation}", client=parse_address("198.51.100.7")
    )
    assert principal.key_id == generated.key_id
