"""Plans (ADR-0043 D3): limits per edition from the deployment's plans file, an operator's
overrides per tenant, and the tenant's effective entitlements written into its snapshot."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from umai_decision_engine.contracts.entitlements import Limits, PlansFile, TenantEntitlements
from umai_decision_engine.db.repositories import TenantInfo

log = logging.getLogger("umai.control_plane.plans")


class Plans:
    """``None`` file: the deployment has no plans, so snapshots carry no entitlements and
    nothing is refused for quota (the self-hosted default)."""

    def __init__(self, plans: PlansFile | None = None) -> None:
        self.file = plans

    @classmethod
    def load(cls, path: Path | None) -> Plans:
        if path is None:
            log.info("plans: no plans file, no quotas")
            return cls()
        plans = PlansFile.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
        log.info("plans: loaded %s (%s)", path, ", ".join(sorted(plans.plans)))
        return cls(plans)

    def limits(self, tenant: TenantInfo) -> Limits:
        """The tenant's effective limits: its edition's plan with its overrides; no plans
        file: none."""

        if self.file is None:
            return Limits()
        base = self.file.plans.get(tenant.edition) or Limits()  # type: ignore[call-overload]
        return base.overridden(tenant.entitlement_overrides or {})

    def entitlements(self, tenant: TenantInfo) -> TenantEntitlements | None:
        """The snapshot block, or ``None`` without a plans file."""

        if self.file is None:
            return None
        return TenantEntitlements(edition=tenant.edition, **self.limits(tenant).model_dump())
