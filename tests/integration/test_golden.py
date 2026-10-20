"""Policy golden-test runner (ADR-0015). All content is synthetic."""

from __future__ import annotations

import hashlib
import json
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.conftest import bundle_yaml
from umai_decision_engine.contracts.common import Action, Layer, Phase, Severity
from umai_decision_engine.contracts.entities import EntitySummary
from umai_decision_engine.contracts.evaluation import (
    BundleInfo,
    Decision,
    ErrorInfo,
    EvaluationResponse,
    LatencyMs,
    OutputModifications,
)
from umai_decision_engine.contracts.policy_tests import Expectation
from umai_decision_engine.golden.runner import compare, format_report, run_golden
from umai_decision_engine.policy import cli

REPO_POLICIES = Path(__file__).resolve().parents[2] / "policies"

GATE = """
on_detect:
  default: BLOCK
  actions: { TR_PHONE: ALLOW_WITH_MODIFICATIONS }
"""

PASSING = """
contract_version: "0.1"
defaults:
  destination: { app: chatgpt.com, class: external_llm }
cases:
  - id: tckn-blocked
    input: { text: "TC kimlik 10000000146" }
    expect: { action: BLOCK, decided_by: L0, entities: [TR_TCKN] }
  - id: phone-masked
    input: { text: "cep 0532 123 45 67" }
    expect:
      action: ALLOW_WITH_MODIFICATIONS
      masked: [TR_PHONE]
      redacted_text: "cep <TR_PHONE_1>"
"""


def _tests(directory: Path, content: str, name: str = "cases.yaml") -> None:
    (directory / "tests").mkdir(exist_ok=True)
    (directory / "tests" / name).write_bytes(textwrap.dedent(content).encode("utf-8"))


def _cases(*cases: str) -> str:
    body = "".join(textwrap.indent(textwrap.dedent(c).strip("\n"), "  ") + "\n" for c in cases)
    return 'contract_version: "0.1"\ncases:\n' + body


@pytest.fixture
def gated(write_bundle: Callable[..., Path]) -> Callable[..., Path]:
    def _make(version: int = 1, *, active: int | None = 1, mode: str = "ENFORCE") -> Path:
        return write_bundle(
            bundle_yaml(version=version, mode=mode, l0_extra=GATE), version=version, active=active
        )

    return _make


async def _run(path: Path, detector) -> object:
    return await run_golden(path, detector=detector)


# ------------------------------------------------------------------ running


async def test_passing_cases(gated, write_bundle, detector) -> None:
    _tests(gated(), PASSING)
    report = await _run(write_bundle.root, detector)
    assert report.ok(require_tests=True), format_report(report)
    assert [c.case_id for c in report.cases] == ["tckn-blocked", "phone-masked"]
    assert report.versions[0].active


async def test_failing_case_is_reported_without_input_text(gated, write_bundle, detector) -> None:
    _tests(
        gated(),
        _cases("""
            - id: tckn-wrongly-expected-allow
              input: { text: "TC kimlik 10000000146" }
              expect: { action: ALLOW, entities: [TR_TCKN, TR_PHONE] }
        """),
    )
    report = await _run(write_bundle.root, detector)
    assert not report.ok()
    (case,) = report.cases
    assert case.failures == (
        "action: expected ALLOW, got BLOCK",
        "entities: expected [TR_PHONE, TR_TCKN], got [TR_TCKN]",
    )
    output = "\n".join(format_report(report))
    assert "FAIL  tckn-wrongly-expected-allow  [tests/cases.yaml]" in output
    assert "10000000146" not in output  # case input is never printed
    assert output.endswith("1 versions, 1 cases: 0 passed, 1 failed")


async def test_monitor_mode_would_have(gated, write_bundle, detector) -> None:
    _tests(
        gated(mode="MONITOR"),
        _cases("""
            - id: monitor-allows-but-records
              input: { text: "TC kimlik 10000000146" }
              expect: { action: ALLOW, would_have: BLOCK, masked: [] }
        """),
    )
    report = await _run(write_bundle.root, detector)
    assert report.ok(), format_report(report)


async def test_l1_not_implemented_is_assertable(write_bundle, detector) -> None:
    rules = (
        "[{id: r1, when: {all: [{field: phase, operator: EQUALS, value: PRE_LLM}]}, effect: BLOCK}]"
    )
    directory = write_bundle(bundle_yaml(l1a_rules=rules), active=1)
    _tests(
        directory,
        _cases("""
            - id: clean-text-reaches-l1
              input: { text: "Toplantı notlarını özetle" }
              expect: { action: BLOCK, decided_by: pipeline, errors: [L1_NOT_IMPLEMENTED] }
        """),
    )
    report = await _run(directory, detector)
    assert report.ok(), format_report(report)


async def test_messages_and_assistant_focus(gated, write_bundle, detector) -> None:
    _tests(
        gated(),
        _cases(
            """
            - id: last-user-message-only
              input:
                messages:
                  - { role: user, content: "TC kimlik 10000000146" }
                  - { role: assistant, content: "Bunu paylaşamam." }
                  - { role: user, content: "Tamam, özetle" }
              expect: { action: ALLOW, entities: [] }
            """,
            """
            - id: assistant-text
              input: { phase: TOOL_INPUT, focus: LAST_ASSISTANT_MESSAGE, text: "TC 10000000146" }
              expect: { action: BLOCK }
            """,
        ),
    )
    report = await _run(write_bundle.root, detector)
    assert report.ok(), format_report(report)


# ------------------------------------------------------------------ load-time errors


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (
            _cases("""
            - id: typo
              input: { text: "x" }
              expect: { action: BLOCK, entites: [TR_TCKN] }
         """),
            "cases.0.expect.entites: Extra inputs are not permitted",
        ),
        (
            _cases("""
            - id: no-decision
              input: { text: "x" }
              expect: { entities: [] }
         """),
            "exactly one of 'action' or 'acceptable'",
        ),
        (
            _cases("""
            - id: no-content
              input: { phase: PRE_LLM }
              expect: { action: ALLOW }
         """),
            "exactly one of 'text' or 'messages'",
        ),
        (
            _cases("""
            - id: not-enabled
              input: { text: "x" }
              expect: { action: BLOCK, entities: [TR_VKN] }
         """),
            "entity types not enabled in the bundle: TR_VKN",
        ),
        (
            'contract_version: "0.1"\ndefaults: { text: "x" }\n'
            "cases: [{id: c, input: {}, expect: {action: BLOCK}}]\n",
            "defaults cannot set",
        ),
        ("cases: [", "invalid YAML"),
        ("", "expected a mapping with 'contract_version' and 'cases'"),
    ],
)
async def test_invalid_test_files(gated, write_bundle, detector, content, error) -> None:
    _tests(gated(), content)
    report = await _run(write_bundle.root, detector)
    assert not report.ok()
    assert any(error in e for e in report.versions[0].errors), report.versions[0].errors


async def test_validation_errors_do_not_echo_input(gated, write_bundle, detector) -> None:
    _tests(
        gated(),
        _cases("""
            - id: bad-focus
              input: { text: "x", focus: "SENTINEL 10000000146" }
              expect: { action: BLOCK }
        """),
    )
    report = await _run(write_bundle.root, detector)
    (error,) = report.versions[0].errors
    assert "cases.0.input.focus" in error
    assert "SENTINEL" not in error


async def test_duplicate_case_ids_across_files(gated, write_bundle, detector) -> None:
    directory = gated()
    _tests(directory, PASSING, "a.yaml")
    _tests(directory, PASSING, "b.yaml")
    report = await _run(write_bundle.root, detector)
    errors = report.versions[0].errors
    assert "tests/b.yaml: case tckn-blocked: duplicate case id (also in tests/a.yaml)" in errors
    assert len(report.cases) == 2  # the duplicates are not run


async def test_invalid_bundle_fails_the_run(write_bundle, detector) -> None:
    directory = write_bundle(bundle_yaml().replace("mode: ENFORCE", "mode: SOMETIMES"), active=1)
    _tests(directory, PASSING)
    report = await _run(write_bundle.root, detector)
    assert not report.ok()
    assert report.versions[0].errors and not report.cases
    assert any("active pointer gr-test -> 1 has no valid bundle" in e for e in report.errors)


async def test_manifest_hash_mismatch_fails(write_bundle, detector) -> None:
    manifest = json.dumps(
        {
            "bundle_id": "gr-test",
            "version": 1,
            "contract_version": "0.4",
            "engine": ">=0.1",
            "created_at": "2026-10-16T09:00:00Z",
            "sha256": "0" * 64,
        }
    )
    write_bundle(bundle_yaml(), active=1, manifest=manifest)
    report = await _run(write_bundle.root, detector)
    assert any("sha256 does not match" in e for e in report.versions[0].errors)


# ------------------------------------------------------------------ selection and require-tests


async def test_require_tests_applies_to_active_versions(gated, write_bundle, detector) -> None:
    gated(1, active=2)
    _tests(gated(2, active=2), PASSING)
    report = await _run(write_bundle.root, detector)
    assert report.ok(require_tests=True)  # v1 is inactive: no tests needed

    untested = await _run(gated(3, active=3).parent, detector)
    assert untested.ok() and not untested.ok(require_tests=True)
    lines = format_report(untested, require_tests=True)
    assert "error: gr-test v3 (active) has no golden tests (--require-tests)" in lines


async def test_version_directory_selects_one_version(gated, write_bundle, detector) -> None:
    gated(1, active=1)
    _tests(gated(2, active=1), PASSING)
    report = await _run(write_bundle.root / "gr-test" / "2", detector)
    assert [(v.version, v.active) for v in report.versions] == [(2, False)]
    assert report.ok()


async def test_bundle_directory_reports_bad_active_pointer(gated, write_bundle, detector) -> None:
    directory = gated(1, active=7)
    report = await _run(directory.parent, detector)
    assert any("active pointer gr-test -> 7 has no valid bundle" in e for e in report.errors)
    assert not report.ok()


async def test_empty_directory(tmp_path, detector) -> None:
    report = await _run(tmp_path, detector)
    assert not report.ok() and "no bundle versions found" in report.errors[0]


def test_cli_exit_codes(gated, write_bundle, capsys) -> None:
    directory = gated()
    _tests(directory, PASSING)
    assert cli.main(["test", str(write_bundle.root), "--require-tests"]) == 0
    assert "2 passed, 0 failed" in capsys.readouterr().out

    _tests(directory, PASSING.replace("action: BLOCK", "action: ALLOW"), "cases.yaml")
    assert cli.main(["test", str(directory)]) == 1
    assert "action: expected ALLOW, got BLOCK" in capsys.readouterr().out


async def test_repository_policies_pass_their_golden_tests(detector) -> None:
    report = await run_golden(REPO_POLICIES, detector=detector)
    assert report.ok(require_tests=True), "\n".join(format_report(report, require_tests=True))


# ------------------------------------------------------------------ compare


def _response(**overrides) -> EvaluationResponse:
    values = {
        "request_id": "r",
        "tenant_id": "t",
        "bundle": BundleInfo(id="b", version=1),
        "phase": Phase.PRE_LLM,
        "decision": Decision(
            action=Action.ALLOW_WITH_MODIFICATIONS,
            allowed=True,
            severity=Severity.MEDIUM,
            reason="L0: TR_PHONE detected",
            decided_by="L0",
        ),
        "entities": [EntitySummary(type="TR_PHONE", count=1, distinct=1)],
        "output_modifications": OutputModifications(
            redacted_text="cep <TR_PHONE_1>", entities=["TR_PHONE"]
        ),
        "event_id": "e",
        "latency_ms": LatencyMs(total=1.0),
    }
    values.update(overrides)
    return EvaluationResponse(**values)


@pytest.mark.parametrize(
    ("expect", "failure"),
    [
        ({"action": "BLOCK"}, "action: expected BLOCK, got ALLOW_WITH_MODIFICATIONS"),
        (
            {"acceptable": ["BLOCK", "STEP_UP_APPROVAL"]},
            "action: expected one of [BLOCK, STEP_UP_APPROVAL], got ALLOW_WITH_MODIFICATIONS",
        ),
        (
            {"action": "ALLOW_WITH_MODIFICATIONS", "decided_by": "L1a"},
            "decided_by: expected L1a, got L0",
        ),
        (
            {"action": "ALLOW_WITH_MODIFICATIONS", "severity": "LOW"},
            "severity: expected LOW, got MEDIUM",
        ),
        (
            {"action": "ALLOW_WITH_MODIFICATIONS", "entities": []},
            "entities: expected [], got [TR_PHONE]",
        ),
        (
            {"action": "ALLOW_WITH_MODIFICATIONS", "masked": ["EMAIL_ADDRESS"]},
            "masked: expected [EMAIL_ADDRESS], got [TR_PHONE]",
        ),
        (
            {"action": "ALLOW_WITH_MODIFICATIONS", "redacted_text": "x"},
            "redacted_text: expected 'x', got 'cep <TR_PHONE_1>'",
        ),
        (
            {"action": "ALLOW_WITH_MODIFICATIONS", "reason_contains": "ex-1"},
            "reason: expected to contain 'ex-1', got 'L0: TR_PHONE detected'",
        ),
        (
            {"action": "ALLOW_WITH_MODIFICATIONS", "would_have": "BLOCK"},
            "would_have: expected BLOCK, got none (not MONITOR mode)",
        ),
        (
            {"action": "ALLOW_WITH_MODIFICATIONS", "errors": ["TIMEOUT"]},
            "errors: expected [TIMEOUT], got []",
        ),
        (
            {"action": "ALLOW_WITH_MODIFICATIONS", "degraded": True},
            "degraded: expected True, got False",
        ),
    ],
)
def test_compare_reports_each_mismatch(expect, failure) -> None:
    assert compare(Expectation.model_validate(expect), _response()) == [failure]


def test_compare_passes_and_handles_missing_modifications() -> None:
    expect = Expectation.model_validate(
        {
            "acceptable": ["ALLOW_WITH_MODIFICATIONS", "BLOCK"],
            "decided_by": "L0",
            "entities": ["TR_PHONE"],
            "masked": ["TR_PHONE"],
            "redacted_text": "cep <TR_PHONE_1>",
            "errors": [],
            "degraded": False,
        }
    )
    assert compare(expect, _response()) == []
    blocked = _response(
        output_modifications=None,
        errors=[ErrorInfo(layer=Layer.L0, type="TIMEOUT")],
    )
    assert compare(
        Expectation(action=Action.ALLOW_WITH_MODIFICATIONS, redacted_text="cep"), blocked
    ) == ["redacted_text: expected a masked text, got no output modifications"]


def test_root_bundles_belong_to_the_deployments_default_tenant(
    gated, write_bundle, monkeypatch, capsys
) -> None:
    """A deployment whose default tenant is not "default" publishes root-level bundles whose
    manifests name it; the runner must own them the same way the engine does."""

    directory = gated()
    _tests(directory, PASSING)
    content = (directory / "bundle.yaml").read_bytes()
    manifest = {
        "bundle_id": "gr-test",
        "version": 1,
        "tenant_id": "acme",
        "contract_version": "0.4",
        "engine": ">=0.1",
        "created_at": "2026-10-20T09:00:00Z",
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert cli.main(["test", str(write_bundle.root), "--default-tenant", "acme"]) == 0
    assert "2 passed, 0 failed" in capsys.readouterr().out
    monkeypatch.setenv("UMAI_DE_DEFAULT_TENANT_ID", "acme")
    assert cli.main(["test", str(write_bundle.root)]) == 0  # from the settings
    capsys.readouterr()
    monkeypatch.setenv("UMAI_DE_DEFAULT_TENANT_ID", "default")
    assert cli.main(["test", str(write_bundle.root)]) == 1
    assert "does not match the owner 'default'" in capsys.readouterr().out
