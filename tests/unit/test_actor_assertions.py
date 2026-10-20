"""On-behalf-of assertions (ADR-0023 §1, contract credentials 0.2): only a correctly signed,
current ES256 assertion for this issuer and audience is accepted. Synthetic subjects only."""

from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from umai_decision_engine.contracts.credentials import ActorAssertion
from umai_decision_engine.control_plane.actors import (
    ActorError,
    load_public_key,
    public_key_b64,
    sign_actor_assertion,
    verify_actor_assertion,
)

ISS, AUD = "umai-console", "umai-policy-service"
NOW = 1_800_000_000


@pytest.fixture(scope="module")
def key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _claims(**overrides) -> ActorAssertion:
    data = {
        "iss": ISS,
        "aud": AUD,
        "sub": "auth0|synthetic-1",
        "email": "ada@example.com",
        "tenant_id": "acme",
        "roles": ["policy_editor"],
        "session_id": "s-1",
        "iat": NOW,
        "exp": NOW + 300,
        "jti": "j-1",
    } | overrides
    return ActorAssertion.model_validate(data)


def _verify(token: str, key, now: float = NOW + 10, **kw):
    keys = {"console-1": load_public_key(public_key_b64(key))}
    return verify_actor_assertion(
        token, keys, issuer=kw.get("issuer", ISS), audience=kw.get("audience", AUD), now=now
    )


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def test_round_trip(key) -> None:
    claims = _verify(sign_actor_assertion(_claims(), key, "console-1"), key)
    assert claims.sub == "auth0|synthetic-1" and claims.tenant_id == "acme"
    assert [r.value for r in claims.roles] == ["policy_editor"]


def test_person_without_tenant_is_valid(key) -> None:
    token = sign_actor_assertion(_claims(tenant_id=None, roles=[]), key, "console-1")
    assert _verify(token, key).tenant_id is None


@pytest.mark.parametrize(
    "overrides",
    [{"exp": NOW + 301}, {"exp": NOW}, {"roles": ["owner"], "tenant_id": None}, {"extra": 1}],
)
def test_claims_rules(overrides) -> None:
    with pytest.raises(ValidationError):
        _claims(**overrides)


def test_expired_and_future(key) -> None:
    token = sign_actor_assertion(_claims(), key, "console-1")
    with pytest.raises(ActorError, match="expired"):
        _verify(token, key, now=NOW + 300 + 31)
    _verify(token, key, now=NOW + 300 + 29)  # inside the clock skew
    with pytest.raises(ActorError, match="future"):
        _verify(token, key, now=NOW - 31)


def test_wrong_issuer_or_audience(key) -> None:
    token = sign_actor_assertion(_claims(), key, "console-1")
    with pytest.raises(ActorError):
        _verify(token, key, issuer="someone-else")
    with pytest.raises(ActorError):
        _verify(token, key, audience="umai-engine")


def test_unknown_key_and_other_key(key) -> None:
    token = sign_actor_assertion(_claims(), key, "console-2")
    with pytest.raises(ActorError, match="unknown"):
        _verify(token, key)
    other = ec.generate_private_key(ec.SECP256R1())
    forged = sign_actor_assertion(_claims(), other, "console-1")
    with pytest.raises(ActorError, match="signature"):
        _verify(forged, key)


def test_tampered_claims(key) -> None:
    header, _, signature = sign_actor_assertion(_claims(), key, "console-1").split(".")
    elevated = _claims(roles=["owner"]).model_dump(mode="json", exclude_none=True)
    with pytest.raises(ActorError, match="signature"):
        _verify(f"{header}.{_b64(elevated)}.{signature}", key)


@pytest.mark.parametrize(
    "header",
    [
        {"alg": "none", "typ": "umai-actor+jwt", "kid": "console-1"},
        {"alg": "HS256", "typ": "umai-actor+jwt", "kid": "console-1"},
        {"alg": "ES256", "typ": "JWT", "kid": "console-1"},
    ],
)
def test_algorithm_and_type_are_fixed(key, header) -> None:
    _, claims, signature = sign_actor_assertion(_claims(), key, "console-1").split(".")
    with pytest.raises(ActorError):
        _verify(f"{_b64(header)}.{claims}.{signature}", key)


@pytest.mark.parametrize("token", ["", "a.b", "a.b.c.d", "x" * 9000, "!!.??.**"])
def test_malformed(key, token: str) -> None:
    with pytest.raises(ActorError):
        _verify(token, key)


def test_only_p256_keys_load() -> None:
    other = ec.generate_private_key(ec.SECP384R1())
    with pytest.raises(ValueError, match="P-256"):
        load_public_key(public_key_b64(other))


def test_real_clock(key) -> None:
    now = int(time.time())
    token = sign_actor_assertion(_claims(iat=now, exp=now + 60), key, "console-1")
    keys = {"console-1": load_public_key(public_key_b64(key))}
    assert verify_actor_assertion(token, keys, issuer=ISS, audience=AUD).sub
