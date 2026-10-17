"""L0 gate: when L0 detects an entity, the decision is made here and L1 does not run (ADR-0014).

Per detected type: the type's rule (guardrail policy, or legacy ``l0.on_detect``) gives the
action, unless the first matching deterministic exception covering that type relaxes it. Final
action = strictest across types. The decision lists what produced it (``matched``, ADR-0021).
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
from umai_decision_engine.contracts.evaluation import Decision, MatchedRule, OutputModifications
from umai_decision_engine.l0.redact import redact_types
from umai_decision_engine.pipeline.l0_policies import TypeRule, type_rules
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


def _rule_label(rule: TypeRule) -> str:
    label = rule.source_name or rule.source_id
    return f"{label} v{rule.source_version}" if rule.source_version else label


def decide_l0(
    config: L0Config,
    l0: EntityDetectionResult,
    facts: Facts,
    phase: Phase,
    scanned_text: str,
    rules: dict[str, TypeRule] | None = None,
) -> GateOutcome:
    detected = [s.type for s in l0.summary]
    assert detected, "the L0 gate only runs when something was detected"
    rules = rules if rules is not None else type_rules(config)

    matching = [ex for ex in config.exceptions if evaluate_group(ex.when, facts)]
    actions: dict[str, Action] = {}
    used: dict[str, str] = {}
    sources: dict[tuple, tuple[MatchedRule, list[str]]] = {}
    for entity_type in detected:
        rule = rules[entity_type]
        exception = next((ex for ex in matching if ex.covers(entity_type)), None)
        if exception is not None:
            action = Action(exception.action)
            used[entity_type] = exception.id
            key = ("l0_exception", exception.id, None)
            template = MatchedRule(kind="l0_exception", id=exception.id, action=action)
        else:
            action = _effective(rule.action, phase)
            key = (rule.kind, rule.source_id, rule.source_version)
            template = MatchedRule(
                kind=rule.kind,  # type: ignore[arg-type]
                id=rule.source_id,
                version=rule.source_version,
                name=rule.source_name,
                action=action,
            )
        actions[entity_type] = action
        sources.setdefault(key, (template, []))[1].append(entity_type)

    final = max(actions.values(), key=lambda a: ACTION_RANK[a])
    strict = [t for t, a in actions.items() if a == final]
    strict_rules = [rules[t] for t in strict if t not in used]
    severity = (
        _RELAXED_SEVERITY.get(final)
        or max((r.severity for r in strict_rules), key=_severity_rank, default=None)
        or config.on_detect.severity
    )

    if used and all(t in used for t in strict):
        ids = sorted({used[t] for t in strict})
        reason = f"L0: {', '.join(strict)} allowed by exception {', '.join(ids)}"
    else:
        reason = f"L0: {', '.join(strict)} detected"
        policies = sorted({_rule_label(r) for r in strict_rules if r.kind == "l0_policy"})
        if policies:
            reason += f" by policy {', '.join(policies)}"

    matched = sorted(
        (template.model_copy(update={"entities": types}) for template, types in sources.values()),
        key=lambda m: (-ACTION_RANK[m.action], m.kind, m.id),
    )

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
        matched=matched,
    )
    return GateOutcome(
        decision=decision, actions=actions, exceptions=used, output_modifications=modifications
    )


_SEVERITY_ORDER = (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)


def _severity_rank(severity: Severity) -> int:
    return _SEVERITY_ORDER.index(severity)
