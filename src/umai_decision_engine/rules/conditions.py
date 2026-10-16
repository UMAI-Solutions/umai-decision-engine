"""Condition evaluation (operators of ``umai-engine``'s AGT rules + ``CONTAINS_ANY``).

Missing facts are conservative: ``EXISTS`` is false, ``NOT_EXISTS`` is true and every other
operator is false — a rule or exception never matches on information the caller did not send.
String comparisons are case-insensitive.
"""

from __future__ import annotations

from typing import Any

import regex

from umai_decision_engine.contracts.bundle import Condition, ConditionGroup
from umai_decision_engine.rules.facts import Facts, lookup


def _norm(value: Any) -> Any:
    return value.casefold() if isinstance(value, str) else value


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple, set, frozenset)) else [value]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_condition(condition: Condition, facts: Facts) -> bool:
    present, actual = lookup(facts, condition.field)
    op = condition.operator
    if op == "EXISTS":
        return present
    if op == "NOT_EXISTS":
        return not present
    if not present:
        return False

    expected = condition.value
    if op == "EQUALS":
        return _norm(actual) == _norm(expected)
    if op == "NOT_EQUALS":
        return _norm(actual) != _norm(expected)
    if op == "IN":
        return _norm(actual) in {_norm(v) for v in _as_list(expected)}
    if op == "NOT_IN":
        return _norm(actual) not in {_norm(v) for v in _as_list(expected)}
    if op == "CONTAINS":
        if isinstance(actual, str):
            return str(expected).casefold() in actual.casefold()
        return _norm(expected) in {_norm(v) for v in _as_list(actual)}
    if op == "CONTAINS_ANY":
        have = {_norm(v) for v in _as_list(actual)}
        return any(_norm(v) in have for v in _as_list(expected))
    if op == "STARTS_WITH":
        return isinstance(actual, str) and actual.casefold().startswith(str(expected).casefold())
    if op == "ENDS_WITH":
        return isinstance(actual, str) and actual.casefold().endswith(str(expected).casefold())
    if op == "MATCHES_REGEX":
        try:
            return bool(regex.search(str(expected), str(actual), timeout=0.05))
        except (regex.error, TimeoutError):
            return False
    left, right = _number(actual), _number(expected)
    if left is None or right is None:
        return False
    return {
        "GT": left > right,
        "GTE": left >= right,
        "LT": left < right,
        "LTE": left <= right,
    }[op]


def evaluate_group(group: ConditionGroup, facts: Facts) -> bool:
    if group.all_ is not None:
        return all(evaluate_condition(c, facts) for c in group.all_)
    return any(evaluate_condition(c, facts) for c in group.any_ or [])
