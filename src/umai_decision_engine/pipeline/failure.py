"""Failure policy resolution (docs/architecture/decision-merge-and-failure.md)."""

from __future__ import annotations

from umai_decision_engine.contracts.common import (
    NON_INTERACTIVE_PHASES,
    Action,
    FailurePolicy,
    Phase,
    Severity,
)
from umai_decision_engine.contracts.evaluation import Decision


def failure_decision(policy: FailurePolicy, phase: Phase, cause: str) -> Decision:
    """Decision when evaluation cannot complete. Never a silent allow: callers mark degraded."""

    if policy == FailurePolicy.FAIL_OPEN:
        return Decision(
            action=Action.ALLOW,
            allowed=True,
            severity=Severity.LOW,
            reason=f"EVALUATION_UNAVAILABLE (fail_open): {cause}",
            decided_by="pipeline",
        )
    if policy == FailurePolicy.STEP_UP and phase not in NON_INTERACTIVE_PHASES:
        return Decision(
            action=Action.STEP_UP_APPROVAL,
            allowed=False,
            severity=Severity.HIGH,
            reason=f"EVALUATION_UNAVAILABLE (step_up): {cause}",
            decided_by="pipeline",
        )
    return Decision(
        action=Action.BLOCK,
        allowed=False,
        severity=Severity.HIGH,
        reason=f"EVALUATION_UNAVAILABLE: {cause}",
        decided_by="pipeline",
    )


def config_failure_decision(error_type: str, message: str) -> Decision:
    """Missing or invalid configuration always blocks (fixed rule)."""

    return Decision(
        action=Action.BLOCK,
        allowed=False,
        severity=Severity.HIGH,
        reason=f"{error_type}: {message}",
        decided_by="pipeline",
    )
