"""On-behalf-of assertions (ADR-0023 §1, contract credentials 0.2).

The console signs a short-lived ES256 JWS for every policy-service call it makes for a person and
sends it as ``X-Umai-Actor`` next to its service credential. The policy service verifies it here
and records ``sub`` as the actor of every change. Implemented on ``cryptography`` alone: one
algorithm (ES256), one ``typ``, keys by ``kid``; anything else is refused, never downgraded."""

from __future__ import annotations

import base64
import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from pydantic import ValidationError

from umai_decision_engine.contracts.credentials import ACTOR_ASSERTION_TYPE, ActorAssertion

ACTOR_HEADER = "X-Umai-Actor"
CLOCK_SKEW_S = 30
_MAX_TOKEN = 8192


class ActorError(ValueError):
    """The assertion is malformed, unsigned, wrongly signed, expired or meant for someone else."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(part: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except (ValueError, TypeError) as exc:
        raise ActorError("malformed assertion") from exc


def load_public_key(spki_b64: str) -> ec.EllipticCurvePublicKey:
    """A P-256 public key from base64 SubjectPublicKeyInfo DER (the format of the settings)."""

    key = serialization.load_der_public_key(base64.b64decode(spki_b64))
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("actor assertion keys must be P-256 (ES256)")
    return key


def public_key_b64(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> str:
    public = key.public_key() if isinstance(key, ec.EllipticCurvePrivateKey) else key
    der = public.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(der).decode("ascii")


def sign_actor_assertion(
    claims: ActorAssertion, private_key: ec.EllipticCurvePrivateKey, key_id: str
) -> str:
    """Compact JWS (tests and tooling; the console signs with its own library)."""

    header = {"alg": "ES256", "typ": ACTOR_ASSERTION_TYPE, "kid": key_id}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(claims.model_dump_json(exclude_none=True).encode())
    )
    der = private_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return signing_input + "." + _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def verify_actor_assertion(
    token: str,
    public_keys: dict[str, ec.EllipticCurvePublicKey],
    *,
    issuer: str,
    audience: str,
    now: float | None = None,
) -> ActorAssertion:
    if not token or len(token) > _MAX_TOKEN or token.count(".") != 2:
        raise ActorError("malformed assertion")
    header_part, claims_part, signature_part = token.split(".")
    try:
        header = json.loads(_b64url_decode(header_part))
    except ValueError as exc:
        raise ActorError("malformed assertion") from exc
    if not isinstance(header, dict) or header.get("alg") != "ES256":
        raise ActorError("assertion must be ES256")
    if header.get("typ") != ACTOR_ASSERTION_TYPE:
        raise ActorError(f"assertion typ must be {ACTOR_ASSERTION_TYPE}")
    key = public_keys.get(header.get("kid") or "")
    if key is None:
        raise ActorError("unknown assertion key")

    signature = _b64url_decode(signature_part)
    if len(signature) != 64:
        raise ActorError("invalid assertion signature")
    der = encode_dss_signature(
        int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    )
    try:
        key.verify(der, f"{header_part}.{claims_part}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise ActorError("invalid assertion signature") from exc

    try:
        claims = ActorAssertion.model_validate_json(_b64url_decode(claims_part))
    except ValidationError as exc:
        raise ActorError("assertion claims are invalid") from exc
    if claims.iss != issuer or claims.aud != audience:
        raise ActorError("assertion is for another issuer or audience")
    current = time.time() if now is None else now
    if claims.iat > current + CLOCK_SKEW_S:
        raise ActorError("assertion issued in the future")
    if claims.exp <= current - CLOCK_SKEW_S:
        raise ActorError("assertion expired")
    return claims
