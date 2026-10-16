"""Bundle manifest: SHA-256 of ``bundle.yaml`` + Ed25519 signature (ADR-0012)."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import Field

from umai_decision_engine.contracts.common import ContractModel


class ManifestError(ValueError):
    """The manifest does not match the bundle or its signature is invalid."""


class BundleManifest(ContractModel):
    bundle_id: str
    version: int = Field(ge=1)
    contract_version: str
    engine: str | None = None
    created_at: datetime | None = None
    sha256: str = Field(min_length=1)
    key_id: str | None = None
    signature: str | None = None

    def signing_payload(self) -> bytes:
        body = self.model_dump(mode="json", exclude={"signature"}, exclude_none=True)
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sign_manifest(manifest: BundleManifest, private_key_b64: str, key_id: str) -> BundleManifest:
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
    unsigned = manifest.model_copy(update={"key_id": key_id, "signature": None})
    signature = key.sign(unsigned.signing_payload())
    return unsigned.model_copy(update={"signature": base64.b64encode(signature).decode()})


def verify_manifest(
    manifest: BundleManifest,
    bundle_bytes: bytes,
    public_keys: dict[str, str],
    *,
    require_signature: bool,
) -> bool:
    """Check hash and signature. Returns True when signed and verified, False when unsigned."""

    if manifest.sha256.lower() != sha256_hex(bundle_bytes):
        raise ManifestError("sha256 does not match bundle.yaml")
    if manifest.signature is None:
        if require_signature:
            raise ManifestError("bundle is not signed")
        return False
    if not manifest.key_id or manifest.key_id not in public_keys:
        raise ManifestError(f"unknown signing key {manifest.key_id!r}")
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_keys[manifest.key_id]))
    try:
        public.verify(base64.b64decode(manifest.signature), manifest.signing_payload())
    except (InvalidSignature, ValueError) as exc:
        raise ManifestError("invalid signature") from exc
    return True
