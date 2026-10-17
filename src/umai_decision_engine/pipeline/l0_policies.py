"""Effective L0 rules per entity type and phase (ADR-0021).

A guardrail (bundle) states what happens to each detected type either through policies
(policy-bundle 0.6: named, versioned, with roles and a sensitivity level) or, for older
bundles, through ``l0.entities`` + ``l0.on_detect``. Both compile to one ``TypeRule`` per type,
so the L0 gate and the detector configuration do not care which form the bundle used.
"""

from __future__ import annotations

from dataclasses import dataclass

from umai_decision_engine.contracts.bundle import L0Config
from umai_decision_engine.contracts.common import Action, Phase, Severity


@dataclass(frozen=True)
class TypeRule:
    entity: str
    action: Action
    severity: Severity
    min_score: float
    phases: frozenset[Phase] | None  # None → every phase the bundle enables
    kind: str  # "l0_policy" | "l0_on_detect"
    source_id: str
    source_version: int | None = None
    source_name: str | None = None

    def applies_to(self, phase: Phase) -> bool:
        return self.phases is None or phase in self.phases


def type_rules(l0: L0Config) -> dict[str, TypeRule]:
    rules: dict[str, TypeRule] = {}
    if l0.policies:
        for policy in l0.policies:
            phases = policy.phases()
            for entity in policy.entities:
                rules[entity] = TypeRule(
                    entity=entity,
                    action=Action(policy.action),
                    severity=policy.severity,
                    min_score=l0.min_score_overrides.get(entity, policy.min_score),
                    phases=phases,
                    kind="l0_policy",
                    source_id=policy.id,
                    source_version=policy.version,
                    source_name=policy.name,
                )
        return rules
    for entity in l0.entities:
        explicit = entity in l0.on_detect.actions
        rules[entity] = TypeRule(
            entity=entity,
            action=l0.on_detect.action_for(entity),
            severity=l0.on_detect.severity,
            min_score=l0.min_score_overrides.get(entity, l0.min_score),
            phases=None,
            kind="l0_on_detect",
            source_id=f"on_detect.actions.{entity}" if explicit else "on_detect.default",
        )
    return rules


def phase_config(l0: L0Config, rules: dict[str, TypeRule], phase: Phase) -> L0Config:
    """The detector configuration for one phase: only the types whose rule screens it, each at
    its rule's minimum score. Built without re-validation (it may enable no type at all)."""

    active = [entity for entity, rule in rules.items() if rule.applies_to(phase)]
    custom = [c for c in l0.custom_recognizers if c.entity in active]
    return l0.model_construct(
        **{
            **{name: getattr(l0, name) for name in type(l0).model_fields},
            "entities": active,
            "custom_recognizers": custom,
            "min_score_overrides": {e: rules[e].min_score for e in active},
        }
    )
