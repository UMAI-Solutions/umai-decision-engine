"""decide rules (decision-questions 0.4): choices, thresholds, conditions, scores, refusals and
review reasons."""

from __future__ import annotations

import pytest

from umai_decision_engine.contracts.bundle import DecisionPolicy
from umai_decision_engine.contracts.questions import Answer
from umai_decision_engine.l1.mapping import decide

POLICY = DecisionPolicy.model_validate(
    {
        "id": "pol",
        "version": 1,
        "phases": ["PRE_LLM"],
        "policy_text": "Test policy.",
        "questions": {
            "intent": {
                "type": "choice",
                "instructions": "Intent?",
                "options": [{"value": "attack"}, {"value": "probe"}, {"value": "benign"}],
            },
            "external": {"type": "condition", "instructions": "External?"},
            "harm": {
                "type": "score",
                "instructions": "Harm?",
                "levels": [{"label": "None"}, {"label": "Low"}, {"label": "High"}],
            },
        },
        "decide": [
            {
                "when": {"intent": {"value": ["attack", "probe"], "p_gte": 0.8}},
                "action": "BLOCK",
                "severity": "HIGH",
                "tags": ["intent.prompt_injection"],
            },
            {"when": {"external": True, "harm": {"gte": 2}}, "action": "STEP_UP_APPROVAL"},
            {"when": {"external": {"p_gte": 0.95}}, "action": "ALLOW_WITH_WARNINGS"},
            {"when": {"intent": "probe"}, "action": "ALLOW_WITH_WARNINGS", "tags": ["x.probe"]},
            {"default": "ALLOW"},
        ],
        "review_when": {"actions": ["BLOCK"], "or_confidence_below": 0.6},
    }
)


def answers(intent=None, external=None, harm=None) -> dict[str, Answer]:
    out = {}
    if intent is not None:
        out["pol.intent"] = intent
    if external is not None:
        out["pol.external"] = external
    if harm is not None:
        out["pol.harm"] = harm
    return out


def choice(value: str, **probabilities: float) -> Answer:
    return Answer(
        value=value,
        probabilities=probabilities or None,
        confidence=max(probabilities.values()) if probabilities else None,
    )


def test_probability_of_several_values_is_summed() -> None:
    outcome = decide(POLICY, answers(choice("benign", attack=0.45, probe=0.4, benign=0.15)))
    assert outcome.action == "BLOCK" and outcome.rule == 0
    assert outcome.probability == pytest.approx(0.85)
    assert outcome.tags == ["intent.prompt_injection"]
    # external / harm unanswered; the winning probability 0.45 is below 0.6
    assert outcome.review_reasons == ["refusal", "low_confidence", "action"]


def test_below_the_threshold_later_rules_apply() -> None:
    outcome = decide(POLICY, answers(choice("probe", attack=0.1, probe=0.6, benign=0.3)))
    assert outcome.action == "ALLOW_WITH_WARNINGS" and outcome.tags == ["x.probe"]


def test_without_probabilities_the_winning_value_counts() -> None:
    assert decide(POLICY, answers(choice("attack"))).action == "BLOCK"


def test_condition_and_score_must_both_hold() -> None:
    both = answers(Answer(value="benign"), Answer(probability=0.7), Answer(level=2))
    assert decide(POLICY, both).action == "STEP_UP_APPROVAL"
    low = answers(Answer(value="benign"), Answer(probability=0.7), Answer(level=1))
    assert decide(POLICY, low).action == "ALLOW"


def test_condition_threshold_override() -> None:
    sure = answers(Answer(value="benign"), Answer(probability=0.96), Answer(level=0))
    assert decide(POLICY, sure).action == "ALLOW_WITH_WARNINGS"


def test_refused_answers_never_match() -> None:
    outcome = decide(
        POLICY, answers(Answer(refused=True), Answer(refused=True), Answer(refused=True))
    )
    assert outcome.action == "ALLOW" and outcome.rule is None
    assert outcome.review_reasons == ["refusal"]


def test_low_confidence_is_a_review_reason() -> None:
    unsure = answers(
        choice("benign", attack=0.3, probe=0.2, benign=0.5),
        Answer(probability=0.1),
        Answer(level=0),
    )
    assert "low_confidence" in decide(POLICY, unsure).review_reasons
