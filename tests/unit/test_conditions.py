"""Condition evaluator shared by the L0 gate exceptions and L1a."""

from __future__ import annotations

import pytest

from umai_decision_engine.contracts.bundle import Condition, ConditionGroup
from umai_decision_engine.rules.conditions import evaluate_condition, evaluate_group

FACTS = {
    "phase": "PRE_LLM",
    "subject.department": "KYC",
    "destination.class": "internal_app",
    "mcp.method": "delete",
    "entities.types": ["TR_TCKN", "EMAIL_ADDRESS"],
    "entities.TR_TCKN.count": 3,
    "entities.TR_TCKN.distinct": 2,
}


def _c(field, operator, value=None) -> Condition:
    return Condition(field=field, operator=operator, value=value)


@pytest.mark.parametrize(
    "condition, expected",
    [
        (_c("subject.department", "EQUALS", "kyc"), True),
        (_c("subject.department", "NOT_EQUALS", "SALES"), True),
        (_c("mcp.method", "IN", ["delete", "drop"]), True),
        (_c("mcp.method", "NOT_IN", ["read"]), True),
        (_c("entities.types", "CONTAINS", "TR_TCKN"), True),
        (_c("entities.types", "CONTAINS_ANY", ["CREDIT_CARD", "EMAIL_ADDRESS"]), True),
        (_c("entities.TR_TCKN.distinct", "GTE", 2), True),
        (_c("entities.TR_TCKN.count", "LT", 3), False),
        (_c("entities.CREDIT_CARD.count", "EQUALS", 0), True),  # undetected type counts as 0
        (_c("destination.class", "STARTS_WITH", "internal"), True),
        (_c("destination.class", "MATCHES_REGEX", "^int.*app$"), True),
        (_c("subject.role", "EXISTS"), False),
        (_c("subject.role", "NOT_EXISTS"), True),
        # missing facts never match (conservative)
        (_c("subject.role", "NOT_EQUALS", "admin"), False),
        (_c("subject.role", "NOT_IN", ["admin"]), False),
        (_c("subject.department", "GT", 1), False),  # non-numeric
    ],
)
def test_operators(condition, expected) -> None:
    assert evaluate_condition(condition, FACTS) is expected


def test_groups() -> None:
    both = ConditionGroup.model_validate(
        {
            "all": [
                {"field": "subject.department", "value": "KYC"},
                {"field": "destination.class", "value": "internal_app"},
            ]
        }
    )
    either = ConditionGroup.model_validate(
        {
            "any": [
                {"field": "subject.department", "value": "SALES"},
                {"field": "destination.class", "value": "internal_app"},
            ]
        }
    )
    assert evaluate_group(both, FACTS)
    assert evaluate_group(either, FACTS)
    assert not evaluate_group(both, {**FACTS, "destination.class": "external_llm"})
