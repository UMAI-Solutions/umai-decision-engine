"""Bundle tooling for hand-made bundles (until the policy control plane exists).

python -m umai_decision_engine.policy.cli keygen
python -m umai_decision_engine.policy.cli validate policies/<bundle_id>/<version>
python -m umai_decision_engine.policy.cli test policies [--require-tests]   # golden tests
python -m umai_decision_engine.policy.cli sign <bundle_dir> --key-id K --private-key-file F
python -m umai_decision_engine.policy.cli verify <bundle_dir> --public-key K=BASE64
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from umai_decision_engine import __version__
from umai_decision_engine.policy.manifest import (
    BundleManifest,
    sha256_hex,
    sign_manifest,
    verify_manifest,
)
from umai_decision_engine.policy.sources import BUNDLE_FILE, MANIFEST_FILE
from umai_decision_engine.policy.store import parse_bundle


def _keygen(_: argparse.Namespace) -> int:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    print(f"private (keep secret, store outside the repo): {base64.b64encode(private).decode()}")
    print(f"public  (UMAI_DE_BUNDLE_PUBLIC_KEYS value):     {base64.b64encode(public).decode()}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    bundle = parse_bundle((Path(args.bundle_dir) / BUNDLE_FILE).read_bytes())
    print(f"ok: {bundle.id} v{bundle.version}")
    return 0


def _test(args: argparse.Namespace) -> int:
    # Imported here: loads Presidio, which keygen/sign/verify do not need.
    from umai_decision_engine.golden.runner import format_report, run_golden

    logging.getLogger("umai.de").setLevel(logging.ERROR)  # the report already lists problems
    keys = dict(item.split("=", 1) for item in args.public_key or [])
    report = asyncio.run(run_golden(Path(args.path), public_keys=keys))
    for line in format_report(report, require_tests=args.require_tests):
        print(line)
    return 0 if report.ok(require_tests=args.require_tests) else 1


def _sign(args: argparse.Namespace) -> int:
    directory = Path(args.bundle_dir)
    data = (directory / BUNDLE_FILE).read_bytes()
    bundle = parse_bundle(data)
    manifest = BundleManifest(
        bundle_id=bundle.id,
        version=bundle.version,
        contract_version=bundle.contract_version,
        engine=f">={__version__}",
        created_at=datetime.now(UTC).replace(microsecond=0),
        sha256=sha256_hex(data),
    )
    private = Path(args.private_key_file).read_text(encoding="utf-8").strip()
    signed = sign_manifest(manifest, private, args.key_id)
    (directory / MANIFEST_FILE).write_text(
        signed.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8"
    )
    print(f"signed {bundle.id} v{bundle.version} with {args.key_id}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    directory = Path(args.bundle_dir)
    keys = dict(item.split("=", 1) for item in args.public_key)
    manifest = BundleManifest.model_validate_json((directory / MANIFEST_FILE).read_bytes())
    verify_manifest(manifest, (directory / BUNDLE_FILE).read_bytes(), keys, require_signature=True)
    parse_bundle((directory / BUNDLE_FILE).read_bytes())
    print(f"ok: signature by {manifest.key_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="umai-de-bundle")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("keygen").set_defaults(func=_keygen)
    p = sub.add_parser("validate")
    p.add_argument("bundle_dir")
    p.set_defaults(func=_validate)
    p = sub.add_parser("test", help="validate bundles and run their golden tests (ADR-0015)")
    p.add_argument("path", help="policies root, a bundle directory or a version directory")
    p.add_argument(
        "--require-tests", action="store_true", help="fail when an active version has no tests"
    )
    p.add_argument("--public-key", action="append", metavar="KEY_ID=BASE64")
    p.set_defaults(func=_test)
    p = sub.add_parser("sign")
    p.add_argument("bundle_dir")
    p.add_argument("--key-id", required=True)
    p.add_argument("--private-key-file", required=True)
    p.set_defaults(func=_sign)
    p = sub.add_parser("verify")
    p.add_argument("bundle_dir")
    p.add_argument("--public-key", action="append", required=True, metavar="KEY_ID=BASE64")
    p.set_defaults(func=_verify)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # Turkish text on a cp1252 pipe (Windows CI)
        sys.stdout.reconfigure(errors="backslashreplace")
    try:
        return args.func(args)
    except Exception as exc:  # CLI: report, don't trace
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
