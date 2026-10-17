"""Semantic bundle validation beyond the schema (docs/contracts/policy-bundle.md).

The same function will be used by the policy control plane before publishing (ADR-0013),
so a bundle that passes here also loads in the engine.
"""

from __future__ import annotations

import re
from typing import Any

import regex

from umai_decision_engine.contracts.bundle import (
    ChoiceQuestion,
    ConditionGroup,
    ConditionQuestion,
    PolicyBundle,
    ScoreQuestion,
)
from umai_decision_engine.l0.recognizers.catalog import SUPPORTED_ENTITIES
from umai_decision_engine.l0.recognizers.custom import VALIDATORS as CUSTOM_VALIDATORS
from umai_decision_engine.l0.recognizers.custom import pattern_problems

_L1A_FACT_PREFIXES = (
    "phase",
    "subject.",
    "destination.",
    "action.",
    "mcp.",
    "memory.",
    "entities.",
    "content.",
)
_L2_REVIEW_FIELDS = {
    "decision.action",
    "decision.severity",
    "tags",
    "l1b.min_confidence",
    "l1b.refused",
    "phase",
    "destination.class",
}
_TAG = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
_SCORE_OPS = {"gte", "gt", "lte", "lt", "eq"}

_DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}")


class BundleValidationError(ValueError):
    """The bundle is well-formed but semantically invalid."""


def _check_tags(where: str, tags: list[str], problems: list[str]) -> None:
    for tag in tags:
        if not _TAG.match(tag):
            problems.append(f"{where}: tag {tag!r} is not a dotted lowercase code")


def _l1a_fields(group: ConditionGroup) -> list[str]:
    return [c.field for c in (group.all_ or group.any_ or [])]


def _check_entity_fact(field: str, entities: set[str], where: str, problems: list[str]) -> None:
    if field == "entities.types":
        return
    parts = field.split(".")
    if len(parts) != 3 or parts[2] not in {"count", "distinct"}:
        problems.append(f"{where}: unknown entity fact {field!r}")
    elif parts[1] not in entities:
        problems.append(f"{where}: {field!r} references an entity not enabled in l0.entities")


def _check_when(
    where: str, when: dict[str, Any], questions: dict[str, Any], problems: list[str]
) -> None:
    for qid, expected in when.items():
        question = questions.get(qid)
        if question is None:
            problems.append(f"{where}: unknown question {qid!r}")
        elif isinstance(question, ChoiceQuestion):
            values = expected if isinstance(expected, list) else [expected]
            options = {o.value for o in question.options}
            for value in values:
                if value not in options:
                    problems.append(f"{where}: {value!r} is not an option of {qid!r}")
        elif isinstance(question, ConditionQuestion):
            if not isinstance(expected, bool):
                problems.append(f"{where}: condition {qid!r} must be compared to true/false")
        elif isinstance(question, ScoreQuestion):
            top = len(question.levels) - 1
            checks = expected if isinstance(expected, dict) else {"eq": expected}
            for op, level in checks.items():
                if op not in _SCORE_OPS:
                    problems.append(f"{where}: unknown score operator {op!r} for {qid!r}")
                elif not isinstance(level, int) or not 0 <= level <= top:
                    problems.append(f"{where}: level {level!r} out of range 0..{top} for {qid!r}")


def validate_bundle(bundle: PolicyBundle) -> list[str]:
    problems: list[str] = []
    entities = set(bundle.l0.entities)

    # L0 ------------------------------------------------------------------
    custom_entities = {c.entity for c in bundle.l0.custom_recognizers}
    for entity in sorted(entities - SUPPORTED_ENTITIES - custom_entities):
        problems.append(f"l0.entities: {entity!r} is not supported by this engine version")
    for entity in bundle.l0.min_score_overrides:
        if entity not in entities:
            problems.append(f"l0.min_score_overrides: {entity!r} is not in l0.entities")
    seen_custom: set[str] = set()
    for recognizer in bundle.l0.custom_recognizers:
        where = f"l0.custom_recognizers[{recognizer.entity}]"
        if recognizer.entity in SUPPORTED_ENTITIES:
            problems.append(f"{where}: {recognizer.entity!r} is a built-in entity type")
        if recognizer.entity in seen_custom:
            problems.append(f"{where}: duplicate custom entity")
        seen_custom.add(recognizer.entity)
        if recognizer.entity not in entities:
            problems.append(f"{where}: {recognizer.entity!r} is not in l0.entities")
        if recognizer.validator is not None and recognizer.validator not in CUSTOM_VALIDATORS:
            problems.append(f"{where}: unknown validator {recognizer.validator!r}")
        for pattern in recognizer.patterns:
            for problem in pattern_problems(pattern.regex):
                problems.append(f"{where}.{pattern.name}: regex {problem}")
    for domain in bundle.l0.allowed_domains:
        if not _DOMAIN.fullmatch(domain):
            problems.append(f"l0.allowed_domains: {domain!r} is not a lowercase domain name")
    if bundle.l0.ner.enabled:
        problems.append("l0.ner: not supported by this engine version")

    # Guardrail policies (ADR-0021) -----------------------------------------
    seen_policies: set[str] = set()
    owner: dict[str, str] = {}
    for policy in bundle.l0.policies:
        where = f"l0.policies[{policy.id}]"
        if policy.id in seen_policies:
            problems.append(f"{where}: duplicate policy id")
        seen_policies.add(policy.id)
        for entity in policy.entities:
            if entity in owner:
                problems.append(f"{where}: {entity!r} is already in policy {owner[entity]!r}")
            owner.setdefault(entity, policy.id)
        if not policy.phases() & set(bundle.phases):
            problems.append(f"{where}: none of its roles maps to a phase this bundle enables")

    # L0 gate (ADR-0014) ----------------------------------------------------
    for entity in bundle.l0.on_detect.actions:
        if entity not in entities:
            problems.append(f"l0.on_detect.actions: {entity!r} is not in l0.entities")
    seen_exceptions: set[str] = set()
    for exception in bundle.l0.exceptions:
        where = f"l0.exceptions[{exception.id}]"
        if exception.id in seen_exceptions:
            problems.append(f"{where}: duplicate exception id")
        seen_exceptions.add(exception.id)
        for entity in exception.entities:
            if entity not in entities:
                problems.append(f"{where}: entity {entity!r} is not in l0.entities")
        for field in _l1a_fields(exception.when):
            if not field.startswith(_L1A_FACT_PREFIXES):
                problems.append(f"{where}: unknown fact path {field!r}")
            elif field.startswith("entities."):
                _check_entity_fact(field, entities, where, problems)

    # L1a -----------------------------------------------------------------
    seen_rules: set[str] = set()
    for rule in bundle.l1a.rules:
        where = f"l1a.rules[{rule.id}]"
        if rule.id in seen_rules:
            problems.append(f"{where}: duplicate rule id")
        seen_rules.add(rule.id)
        for field in _l1a_fields(rule.when):
            if not field.startswith(_L1A_FACT_PREFIXES):
                problems.append(f"{where}: unknown fact path {field!r}")
            elif field.startswith("entities."):
                _check_entity_fact(field, entities, where, problems)
        for condition in rule.when.all_ or rule.when.any_ or []:
            if condition.operator == "MATCHES_REGEX":
                try:
                    regex.compile(str(condition.value))
                except regex.error as exc:
                    problems.append(f"{where}: invalid regex: {exc}")
        for entity in rule.redact:
            if entity not in entities:
                problems.append(f"{where}: redact type {entity!r} is not in l0.entities")
        _check_tags(where, rule.tags, problems)
    if bundle.l1a.legacy_preflight:
        for rule in bundle.l1a.legacy_preflight.rules:
            if rule.mode == "REGEX":
                try:
                    regex.compile(rule.pattern)
                except regex.error as exc:
                    problems.append(f"l1a.legacy_preflight[{rule.id}]: invalid regex: {exc}")

    # L1b -----------------------------------------------------------------
    seen_policies: set[str] = set()
    for policy in bundle.l1b.policies:
        where = f"l1b.policies[{policy.id}]"
        if policy.id in seen_policies:
            problems.append(f"{where}: duplicate policy id")
        seen_policies.add(policy.id)
        for phase in policy.phases:
            if phase not in bundle.phases:
                problems.append(f"{where}: phase {phase} is not in the bundle phases")
        for index, rule in enumerate(policy.decide):
            if rule.when is not None:
                _check_when(f"{where}.decide[{index}]", rule.when, policy.questions, problems)
            for entity in rule.redact:
                if entity not in entities:
                    problems.append(f"{where}: redact type {entity!r} is not in l0.entities")
            _check_tags(f"{where}.decide[{index}]", rule.tags, problems)
    if bundle.l1b.policies and bundle.l1b.enabled and not bundle.l1b.provider:
        problems.append("l1b.provider: required when l1b has policies")

    # L2 ------------------------------------------------------------------
    if bundle.l2.review_when is not None:
        for field in _l1a_fields(bundle.l2.review_when):
            if field not in _L2_REVIEW_FIELDS:
                problems.append(f"l2.review_when: unknown field {field!r}")
    if bundle.l2.enabled and not bundle.l2.model_ref:
        problems.append("l2.model_ref: required when l2 is enabled")

    return problems
