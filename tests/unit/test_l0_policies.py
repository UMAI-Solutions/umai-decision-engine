"""Guardrail policies (policy-bundle 0.6, ADR-0021): rules per type, roles → phases,
sensitivity → minimum score, provenance, validation. Synthetic data only."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from umai_decision_engine.contracts.bundle import L0Config, L0Policy, PolicyBundle
from umai_decision_engine.contracts.common import Action, Phase
from umai_decision_engine.pipeline.l0_policies import phase_config, type_rules
from umai_decision_engine.policy.validate import validate_bundle


def _bundle(l0: str, phases: str = "[PRE_LLM, POST_LLM, TOOL_INPUT, TOOL_OUTPUT]") -> PolicyBundle:
    text = f"""
contract_version: "0.6"
id: gr-test
version: 1
mode: ENFORCE
phases: {phases}
l0:
{l0}
"""
    return PolicyBundle.model_validate(yaml.safe_load(text))


POLICIES = """
  policies:
    - {id: ids, version: 3, name: Kimlik, entities: [TR_TCKN, US_SSN], action: BLOCK}
    - {id: contact, entities: [EMAIL_ADDRESS, TR_PHONE], action: ALLOW_WITH_MODIFICATIONS,
       severity: MEDIUM, roles: [user, assistant], sensitivity: L1}
    - {id: links, entities: [UNKNOWN_LINK], roles: [assistant, tool], sensitivity: L4}
"""


def test_entities_are_derived_from_policies() -> None:
    bundle = _bundle(POLICIES)
    assert bundle.l0.entities == ["TR_TCKN", "US_SSN", "EMAIL_ADDRESS", "TR_PHONE", "UNKNOWN_LINK"]
    assert validate_bundle(bundle) == []


def test_type_rules_from_policies() -> None:
    rules = type_rules(_bundle(POLICIES).l0)
    tckn, email, link = rules["TR_TCKN"], rules["EMAIL_ADDRESS"], rules["UNKNOWN_LINK"]
    assert (tckn.kind, tckn.source_id, tckn.source_version, tckn.source_name) == (
        "l0_policy",
        "ids",
        3,
        "Kimlik",
    )
    assert tckn.action == Action.BLOCK and tckn.min_score == 0.5  # default L3
    assert email.action == Action.ALLOW_WITH_MODIFICATIONS and email.min_score == 0.8  # L1
    assert link.min_score == 0.35  # L4
    assert email.applies_to(Phase.PRE_LLM) and not email.applies_to(Phase.TOOL_INPUT)
    assert link.applies_to(Phase.TOOL_OUTPUT) and not link.applies_to(Phase.PRE_LLM)


def test_phase_config_only_enables_types_for_the_phase() -> None:
    l0 = _bundle(POLICIES).l0
    rules = type_rules(l0)
    assert phase_config(l0, rules, Phase.PRE_LLM).entities == [
        "TR_TCKN",
        "US_SSN",
        "EMAIL_ADDRESS",
        "TR_PHONE",
    ]
    tool_input = phase_config(l0, rules, Phase.TOOL_INPUT)
    assert tool_input.entities == ["TR_TCKN", "US_SSN"]
    assert tool_input.min_score_overrides == {"TR_TCKN": 0.5, "US_SSN": 0.5}
    assert phase_config(l0, rules, Phase.POST_LLM).min_score_overrides["UNKNOWN_LINK"] == 0.35


def test_legacy_bundles_compile_to_on_detect_rules() -> None:
    l0 = L0Config(
        entities=["TR_TCKN", "EMAIL_ADDRESS"],
        min_score_overrides={"TR_TCKN": 0.7},
        on_detect={"actions": {"EMAIL_ADDRESS": "ALLOW_WITH_MODIFICATIONS"}},
    )
    rules = type_rules(l0)
    assert rules["TR_TCKN"].source_id == "on_detect.default" and rules["TR_TCKN"].min_score == 0.7
    assert rules["EMAIL_ADDRESS"].source_id == "on_detect.actions.EMAIL_ADDRESS"
    assert all(
        r.kind == "l0_on_detect" and r.applies_to(Phase.MEMORY_WRITE) for r in rules.values()
    )


@pytest.mark.parametrize(
    ("l0", "problem"),
    [
        (
            """
  policies:
    - {id: a, entities: [TR_TCKN]}
    - {id: a, entities: [US_SSN]}
""",
            "duplicate policy id",
        ),
        (
            """
  policies:
    - {id: a, entities: [TR_TCKN]}
    - {id: b, entities: [TR_TCKN]}
""",
            "'TR_TCKN' is already in policy 'a'",
        ),
        (
            """
  policies:
    - {id: a, entities: [TR_TCKN], roles: [memory]}
""",
            "none of its roles maps to a phase",
        ),
        (
            """
  policies:
    - {id: a, entities: [TR_PASSPORT]}
""",
            "'TR_PASSPORT' is not supported",
        ),
    ],
)
def test_policy_validation(l0, problem) -> None:
    problems = validate_bundle(_bundle(l0))
    assert any(problem in p for p in problems), problems


def test_policies_exclude_entities_and_on_detect_actions() -> None:
    with pytest.raises(ValidationError, match=r"do not set l0.entities"):
        L0Config(entities=["TR_TCKN"], policies=[L0Policy(id="a", entities=["US_SSN"])])
    with pytest.raises(ValidationError, match="needs entities"):
        L0Config()
