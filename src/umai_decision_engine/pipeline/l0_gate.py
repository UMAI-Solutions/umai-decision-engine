"""L0 gate: when L0 detects an entity, the decision is made here and L1 does not run (ADR-0014).

Per detected type: ``l0.on_detect`` action (default BLOCK), unless the first matching
deterministic exception covering that type relaxes it. Final action = strictest across types.
No model calls: this is rule evaluation over facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from umai_decision_engine.contracts.bundle import L0Config
from umai_decision_engine.contracts.common import (
    ACTION_RANK,
    ALLOWED_ACTIONS,
    NON_INTERACTIVE_PHASES,
    Action,
    Phase,
    Severity,
)
from umai_decision_engine.contracts.entities import EntityDetectionResult
from umai_decision_engine.contracts.evaluation import Decision, OutputModifications
from umai_decision_engine.l0.redact import redact_types
from umai_decision_engine.rules.conditions import evaluate_group
from umai_decision_engine.rules.facts import Facts

_RELAXED_SEVERITY = {
    Action.ALLOW: Severity.LOW,
    Action.ALLOW_WITH_WARNINGS: Severity.MEDIUM,
    Action.ALLOW_WITH_MODIFICATIONS: Severity.MEDIUM,
}


@dataclass(frozen=True)
class GateOutcome:
    decision: Decision
    actions: dict[str, Action]  # per detected type, after exceptions
    exceptions: dict[str, str] = field(default_factory=dict)  # type → exception id
    output_modifications: OutputModifications | None = None


def _effective(action: Action, phase: Phase) -> Action:
    # step_up needs a user interface; elsewhere it behaves as BLOCK.
    if action == Action.STEP_UP_APPROVAL and phase in NON_INTERACTIVE_PHASES:
        return Action.BLOCK
    return action


def decide_l0(
    config: L0Config,
    l0: EntityDetectionResult,
    facts: Facts,
    phase: Phase,
    scanned_text: str,
) -> GateOutcome:
    detected = [s.type for s in l0.summary]
    assert detected, "the L0 gate only runs when something was detected"

    matching = [ex for ex in config.exceptions if evaluate_group(ex.when, facts)]
    actions: dict[str, Action] = {}
    used: dict[str, str] = {}
    for entity_type in detected:
        exception = next((ex for ex in matching if ex.covers(entity_type)), None)
        if exception is not None:
            actions[entity_type] = Action(exception.action)
            used[entity_type] = exception.id
        else:
            actions[entity_type] = _effective(config.on_detect.action_for(entity_type), phase)

    final = max(actions.values(), key=lambda a: ACTION_RANK[a])
    strict = [t for t, a in actions.items() if a == final]
    severity = _RELAXED_SEVERITY.get(final, config.on_detect.severity)

    if used and all(t in used for t in strict):
        ids = sorted({used[t] for t in strict})
        reason = f"L0: {', '.join(strict)} allowed by exception {', '.join(ids)}"
    else:
        reason = f"L0: {', '.join(strict)} detected"

    modifications = None
    if final == Action.ALLOW_WITH_MODIFICATIONS:
        masked = {t for t, a in actions.items() if a == Action.ALLOW_WITH_MODIFICATIONS}
        modifications = OutputModifications(
            redacted_text=redact_types(scanned_text, l0.entities, masked),
            entities=sorted(masked),
        )

    decision = Decision(
        action=final,
        allowed=final in ALLOWED_ACTIONS,
        severity=severity,
        reason=reason,
        decided_by="L0",
    )
    return GateOutcome(
        decision=decision, actions=actions, exceptions=used, output_modifications=modifications
    )
