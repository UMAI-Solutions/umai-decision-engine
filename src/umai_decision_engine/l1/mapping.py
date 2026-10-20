"""Map a policy's answers to an action with its ``decide`` rules (decision-questions 0.4).

A rule matches when every question it names has an answer that satisfies it; a refused or
missing answer never matches, so the policy falls through to its default. Choices compare the
winning value, or with ``p_gte`` the probability of the named value(s); conditions compare
``probability >= condition_threshold`` (or ``p_gte``); scores compare the winning level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from umai_decision_engine.contracts.bundle import (
    ChoiceQuestion,
    ConditionQuestion,
    DecisionPolicy,
    ScoreQuestion,
)
from umai_decision_engine.contracts.common import Action, Severity
from umai_decision_engine.contracts.questions import Answer

_SCORE_OPS = {
    "eq": lambda a, b: a == b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
}


@dataclass(frozen=True)
class PolicyOutcome:
    policy: DecisionPolicy
    action: Action
    severity: Severity
    tags: list[str]
    rule: int | None  # index of the matching decide rule; None: the default
    probability: float | None  # the deciding answer's probability, when one rule question has it
    review_reasons: list[str] = field(default_factory=list)

    @property
    def shadow(self) -> bool:
        return self.policy.enforcement == "shadow"


def _choice_probability(answer: Answer, values: list[str]) -> float | None:
    if isinstance(answer.probabilities, dict):
        return sum(answer.probabilities.get(v, 0.0) for v in values)
    return None


def _holds(question: Any, answer: Answer, expected: Any, policy: DecisionPolicy) -> bool:
    if isinstance(question, ChoiceQuestion):
        if isinstance(expected, dict):
            wanted = expected["value"]
            values = wanted if isinstance(wanted, list) else [wanted]
            p = _choice_probability(answer, values)
            if p is None:  # no probabilities: fall back to the winning value
                return answer.value in values
            return p >= expected["p_gte"]
        values = expected if isinstance(expected, list) else [expected]
        return answer.value in values
    if isinstance(question, ConditionQuestion):
        if answer.probability is None:
            return False
        if isinstance(expected, dict):
            return answer.probability >= expected["p_gte"]
        return (answer.probability >= policy.condition_threshold) is expected
    if isinstance(question, ScoreQuestion):
        if answer.level is None:
            return False
        checks = expected if isinstance(expected, dict) else {"eq": expected}
        return all(_SCORE_OPS[op](answer.level, level) for op, level in checks.items())
    return False


def _probability(question: Any, answer: Answer, expected: Any) -> float | None:
    if isinstance(question, ChoiceQuestion):
        wanted = expected["value"] if isinstance(expected, dict) else expected
        return _choice_probability(answer, wanted if isinstance(wanted, list) else [wanted])
    if isinstance(question, ConditionQuestion):
        return answer.probability
    return answer.confidence


def _confidence(question: Any, answer: Answer) -> float | None:
    if isinstance(question, ConditionQuestion):
        p = answer.probability
        return None if p is None else max(p, 1 - p)
    return answer.confidence


def decide(policy: DecisionPolicy, answers: dict[str, Answer]) -> PolicyOutcome:
    """``answers`` by namespaced question id (``<policy id>.<question id>``)."""

    mine = {qid: answers.get(f"{policy.id}.{qid}") for qid in policy.questions}
    reasons: list[str] = []
    if any(a is None or a.refused for a in mine.values()):
        reasons.append("refusal")
    threshold = policy.review_when.or_confidence_below
    if threshold is not None:
        for qid, answer in mine.items():
            if answer is None or answer.refused:
                continue
            confidence = _confidence(policy.questions[qid], answer)
            if confidence is not None and confidence < threshold:
                reasons.append("low_confidence")
                break

    for index, rule in enumerate(policy.decide):
        if rule.default is not None:
            action, severity, tags, matched, probability = (
                rule.default,
                Severity.LOW,
                [],
                None,
                None,
            )
            break
        assert rule.when is not None and rule.action is not None
        ok = True
        probability = None
        for qid, expected in rule.when.items():
            answer = mine.get(qid)
            question = policy.questions[qid]
            if answer is None or answer.refused or not _holds(question, answer, expected, policy):
                ok = False
                break
            if len(rule.when) == 1:
                probability = _probability(question, answer, expected)
        if ok:
            action, severity, tags, matched = rule.action, rule.severity, list(rule.tags), index
            break
    if action in policy.review_when.actions:
        reasons.append("action")
    return PolicyOutcome(
        policy=policy,
        action=action,
        severity=severity,
        tags=tags,
        rule=matched,
        probability=probability,
        review_reasons=reasons,
    )
