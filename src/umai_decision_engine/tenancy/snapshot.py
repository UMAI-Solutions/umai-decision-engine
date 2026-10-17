"""Signing and verification of key-registry snapshots: the same Ed25519 keys and trust
list as bundle manifests (ADR-0012, ADR-0023 §4)."""

from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from umai_decision_engine.contracts.credentials import KeyRegistrySnapshot


class SnapshotError(Exception):
    """The snapshot is unsigned where a signature is required, or the signature is invalid."""


def sign_snapshot(
    snapshot: KeyRegistrySnapshot, private_key_b64: str, key_id: str
) -> KeyRegistrySnapshot:
    unsigned = snapshot.model_copy(update={"signing_key_id": key_id, "signature": None})
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
    signature = private.sign(unsigned.signing_payload())
    return unsigned.model_copy(update={"signature": base64.b64encode(signature).decode()})


def verify_snapshot(
    snapshot: KeyRegistrySnapshot, public_keys: dict[str, str], *, require_signed: bool
) -> bool:
    """Returns whether the snapshot is signed by a trusted key. Raises when a signature is
    present but wrong, or absent while required."""

    if snapshot.signature is None:
        if require_signed:
            raise SnapshotError("snapshot is not signed")
        return False
    if not snapshot.signing_key_id or snapshot.signing_key_id not in public_keys:
        raise SnapshotError(f"unknown signing key {snapshot.signing_key_id!r}")
    public = Ed25519PublicKey.from_public_bytes(
        base64.b64decode(public_keys[snapshot.signing_key_id])
    )
    try:
        public.verify(base64.b64decode(snapshot.signature), snapshot.signing_payload())
    except InvalidSignature as exc:
        raise SnapshotError("snapshot signature is invalid") from exc
    return True
