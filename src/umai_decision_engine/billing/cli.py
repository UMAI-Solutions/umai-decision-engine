"""Billing catalogue commands (ADR-0044 D3): create the provider's product and pricing plans from
the plans file, and check that the configured references charge what the plans file says.

    python -m umai_decision_engine.billing.cli sync [--product NAME]  # prints the plan refs
    python -m umai_decision_engine.billing.cli check                  # one line per paid plan

Reads the plans file and the iyzico keys from the settings (``UMAI_DE_PLANS_FILE``,
``UMAI_DE_IYZICO_*``). A new price is a new provider plan: ``sync`` never edits one."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from umai_decision_engine.billing.iyzico import IyzicoClient, IyzicoError
from umai_decision_engine.control_plane.plans import Plans
from umai_decision_engine.settings import Settings, get_settings


def _client(cfg: Settings) -> IyzicoClient:
    if not (cfg.iyzico_api_key and cfg.iyzico_secret_key):
        sys.exit("UMAI_DE_IYZICO_API_KEY and UMAI_DE_IYZICO_SECRET_KEY are required")
    return IyzicoClient(
        cfg.iyzico_base_url,
        cfg.iyzico_api_key.get_secret_value(),
        cfg.iyzico_secret_key.get_secret_value(),
        timeout_s=cfg.iyzico_timeout_s,
    )


async def sync(cfg: Settings, product_name: str) -> dict[str, str]:
    plans = Plans.load(cfg.plans_file).paid()
    if not plans:
        sys.exit("the plans file has no plan with a billing block")
    client = _client(cfg)
    try:
        product = await client.create_product(product_name, "UMAI AI security, monthly plans")
        refs = {}
        for edition, plan in sorted(plans.items()):
            billing = plan.billing
            name = f"{product_name} {edition} {billing.price} {billing.currency} {billing.interval}"
            refs[edition] = await client.create_pricing_plan(
                product, name, billing.price, billing.currency, billing.interval
            )
        return refs
    finally:
        await client.aclose()


async def check(cfg: Settings) -> int:
    plans = Plans.load(cfg.plans_file).paid()
    client = _client(cfg)
    failures = 0
    try:
        for edition, plan in sorted(plans.items()):
            reference = cfg.billing_plan_refs.get(edition)
            if not reference:
                print(f"{edition}: no reference in UMAI_DE_BILLING_PLAN_REFS")
                failures += 1
                continue
            try:
                provider = await client.pricing_plan(reference)
            except IyzicoError as exc:
                print(f"{edition}: {reference} unreadable ({exc.code})")
                failures += 1
                continue
            ok = (provider.price, provider.currency, provider.interval) == (
                plan.billing.price,
                plan.billing.currency,
                plan.billing.interval,
            )
            failures += 0 if ok else 1
            print(
                f"{edition}: {reference} {provider.price} {provider.currency} {provider.interval}"
                f" {'ok' if ok else 'DIFFERS from the plans file'}"
            )
    finally:
        await client.aclose()
    return failures


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="umai_decision_engine.billing.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    sync_cmd = commands.add_parser("sync", help="create the product and plans at the provider")
    sync_cmd.add_argument("--product", default="UMAI")
    commands.add_parser("check", help="compare the configured plans with the plans file")
    args = parser.parse_args(argv)
    cfg = get_settings()
    if args.command == "sync":
        refs = asyncio.run(sync(cfg, args.product))
        print(f"UMAI_DE_BILLING_PLAN_REFS={json.dumps(refs, separators=(',', ':'))}")
    else:
        sys.exit(1 if asyncio.run(check(cfg)) else 0)


if __name__ == "__main__":
    main()
