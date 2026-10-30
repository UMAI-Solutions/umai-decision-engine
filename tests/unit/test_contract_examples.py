"""Doc examples are tests: the payloads in docs/contracts and docs/architecture must parse."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

import pytest
import yaml

from umai_decision_engine.contracts.bundle import (
    CONTRACT_VERSION,
    ConditionGroup,
    DecideRule,
    L1aConfig,
    L1bConfig,
    PolicyBundle,
)
from umai_decision_engine.contracts.entities import EntityDetectionResult
from umai_decision_engine.contracts.entitlements import (
    EntitlementsView,
    PlansFile,
    PlanUpdate,
    TenantEntitlements,
)
from umai_decision_engine.contracts.evaluation import (
    EvaluationRequest,
    EvaluationResponse,
    InputTooLargeBody,
    QuotaExceededBody,
    ValidationErrorBody,
)
from umai_decision_engine.contracts.events import (
    DecisionEvent,
    DecisionEventPage,
    DecisionEventSummary,
    L1bOutcomeRecord,
    UsageRecord,
    UsageSummary,
)
from umai_decision_engine.contracts.policy_tests import GoldenCase, PolicyTestFile
from umai_decision_engine.policy.manifest import BundleManifest
from umai_decision_engine.policy.validate import validate_bundle

DOCS = Path(__file__).resolve().parents[2] / "docs"
_FENCE = re.compile(r"```(json|yaml)\n(.*?)```", re.S)


def _blocks(relative: str, kind: str) -> list[str]:
    text = (DOCS / relative).read_text(encoding="utf-8")
    return [body for lang, body in _FENCE.findall(text) if lang == kind]


def test_evaluation_api_examples() -> None:
    blocks = _blocks("contracts/evaluation-api.md", "json")
    request, response, *_ = blocks
    EvaluationRequest.model_validate_json(request)
    EvaluationResponse.model_validate_json(response)
    (error,) = [block for block in blocks if '"invalid_request"' in block]
    ValidationErrorBody.model_validate_json(error)
    (too_large,) = [block for block in blocks if '"input_too_large"' in block]
    InputTooLargeBody.model_validate_json(too_large)
    (over_quota,) = [block for block in blocks if '"quota_exceeded"' in block]
    QuotaExceededBody.model_validate_json(over_quota)


def test_entity_detection_example() -> None:
    (example,) = _blocks("contracts/entity-detection.md", "json")
    EntityDetectionResult.model_validate_json(example)


def test_policy_tests_example() -> None:
    example, *cases = _blocks("contracts/policy-tests.md", "yaml")
    test_file = PolicyTestFile.model_validate(yaml.safe_load(example))
    for case in test_file.cases:
        case.input.merged_over(test_file.defaults)
    assert len(cases) == 1  # the model_answers example (0.3)
    for case in yaml.safe_load(cases[0]):
        GoldenCase.model_validate(case).input.merged_over(test_file.defaults)


def test_policy_bundle_examples() -> None:
    (manifest,) = _blocks("contracts/policy-bundle.md", "json")
    BundleManifest.model_validate_json(manifest)
    shapes = _blocks("contracts/policy-bundle.md", "yaml")
    assert len(shapes) == 2  # legacy shape (≤ 0.5) and guardrail policies (0.6)
    for shape in shapes:
        bundle = PolicyBundle.model_validate(yaml.safe_load(shape))
        assert validate_bundle(bundle) == []


def test_policy_bundle_contract_version_is_current() -> None:
    # The policy service stamps CONTRACT_VERSION on every publish: it must be the newest version
    # the model accepts and the one the contract document declares.
    accepted = get_args(PolicyBundle.model_fields["contract_version"].annotation)
    assert accepted[-1] == CONTRACT_VERSION
    text = (DOCS / "contracts/policy-bundle.md").read_text(encoding="utf-8")
    assert f"**Version:** {CONTRACT_VERSION}\n" in text


def test_tagged_action_examples() -> None:
    event, outcome, page, summary = (
        json.loads(b) for b in _blocks("contracts/tagged-action.md", "json")
    )
    assert DecisionEvent.model_validate(event).contract_version == "0.5"
    assert L1bOutcomeRecord.model_validate(outcome).outcome == "decided"
    assert DecisionEventPage.model_validate(page).events[0].source == "gateway"
    parsed = DecisionEventSummary.model_validate(summary)
    assert parsed.total == sum(parsed.by_action.values()) == sum(parsed.by_source.values())
    assert parsed.total == sum(sum(d.actions.values()) for d in parsed.by_day)


def test_usage_examples() -> None:
    usage, summary = (json.loads(b) for b in _blocks("contracts/usage.md", "json"))
    assert UsageRecord.model_validate(usage).meter == "evaluations"
    parsed = UsageSummary.model_validate(summary)
    assert parsed.totals["evaluations"] == sum(
        g.quantity for g in parsed.groups if g.meter == "evaluations"
    )
    assert parsed.totals["evaluations"] == sum(d.meters["evaluations"] for d in parsed.daily)


def test_l1_doc_examples() -> None:
    l1a_block, l1b_block = _blocks("architecture/l1-policy-decision.md", "yaml")
    L1aConfig.model_validate(yaml.safe_load(l1a_block)["l1a"])
    L1bConfig.model_validate(yaml.safe_load(l1b_block)["l1b"])


def test_decision_question_rules_example() -> None:
    (decide_block,) = _blocks("contracts/decision-questions.md", "yaml")
    for rule in yaml.safe_load(decide_block)["decide"]:
        DecideRule.model_validate(rule)


def test_l2_review_when_example() -> None:
    (block, *_) = _blocks("architecture/l2-llm-analyst.md", "yaml")
    ConditionGroup.model_validate(yaml.safe_load(block)["l2"]["review_when"])


@pytest.mark.parametrize("path", sorted((DOCS.parent / "policies").glob("*/*/bundle.yaml")))
def test_repository_bundles_are_valid(path: Path) -> None:
    bundle = PolicyBundle.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert validate_bundle(bundle) == []
    assert (bundle.id, str(bundle.version)) == (path.parent.parent.name, path.parent.name)


def test_json_examples_are_json() -> None:
    for doc in (DOCS / "contracts").glob("*.md"):
        for body in _blocks(f"contracts/{doc.name}", "json"):
            if "/*" in body or "…" in body.split('"')[0]:
                continue
            json.loads(body)


def test_organizations_examples() -> None:
    from umai_decision_engine.contracts.organizations import (
        Domain,
        Invitation,
        InvitationCreate,
        Membership,
        MyOrganization,
        Organization,
        OrganizationCreate,
    )

    blocks = _blocks("contracts/organizations.md", "json")
    models = [
        Organization,
        MyOrganization,
        Membership,
        Invitation,
        Domain,
        OrganizationCreate,
        InvitationCreate,
    ]
    assert len(blocks) == len(models)
    for model, block in zip(models, blocks, strict=True):
        model.model_validate_json(block)


def test_credentials_examples() -> None:
    from umai_decision_engine.contracts.credentials import ActorAssertion

    record, snapshot, assertion = _blocks("contracts/credentials.md", "json")
    json.loads(record)
    TenantEntitlements.model_validate(json.loads(snapshot)["entitlements"])
    ActorAssertion.model_validate_json(assertion)


def test_console_sessions_examples() -> None:
    import re as _re

    from umai_decision_engine.contracts.credentials import ActorAssertion
    from umai_decision_engine.contracts.sessions import (
        SESSION_ID_PATTERN,
        ConsoleSession,
        SessionCheck,
        SessionStart,
        SessionStatus,
    )

    record, start, check, status = _blocks("contracts/console-sessions.md", "json")
    assert ConsoleSession.model_validate_json(record).current
    SessionStart.model_validate_json(start)
    SessionCheck.model_validate_json(check)
    assert SessionStatus.model_validate_json(status).state == "active"
    # The assertion's example session id is one the console would make (credentials 0.8).
    *_, assertion = _blocks("contracts/credentials.md", "json")
    assert _re.match(SESSION_ID_PATTERN, ActorAssertion.model_validate_json(assertion).session_id)


def test_billing_examples() -> None:
    from umai_decision_engine.contracts.billing import BillingView

    (view,) = _blocks("contracts/billing.md", "json")
    parsed = BillingView.model_validate_json(view)
    assert parsed.subscription is not None and parsed.subscription.status == "active"
    assert parsed.plans[0].edition == parsed.edition == "pro"


def test_entitlements_examples() -> None:
    (plans,) = _blocks("contracts/entitlements.md", "yaml")
    block, view, update = _blocks("contracts/entitlements.md", "json")
    parsed = PlansFile.model_validate(yaml.safe_load(plans))
    assert parsed.plans["community"].monthly_requests == 10_000
    # the doc's plans are the shipped ones (deploy/plans.yaml)
    shipped = yaml.safe_load((DOCS.parent / "deploy" / "plans.yaml").read_text(encoding="utf-8"))
    assert PlansFile.model_validate(shipped) == parsed
    assert TenantEntitlements.model_validate_json(block).edition == "community"
    shown = EntitlementsView.model_validate_json(view)
    assert shown.limits == parsed.plans["community"].limits()
    assert PlanUpdate.model_validate_json(update).override_values() == {"monthly_requests": 50_000}
