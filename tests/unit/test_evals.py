"""Eval harness: span metrics, dataset invariants, baseline, suite smoke runs. Synthetic data."""

from __future__ import annotations

import argparse
import json

import pytest

import evals.common
from evals import stack, stubs
from evals.baselines import naive_regex
from evals.common import L0_DATASET, load_cases
from evals.datasets import generate_l0_pii_tr as generator
from evals.metrics import Scores, Span, TypeScore, match_case, overlap_ratio
from evals.suites import l0_pii_tr, leak, leak_errors, load
from umai_decision_engine.policy.store import parse_bundle

# ------------------------------------------------------------------ metrics


def test_overlap_ratio_uses_the_longer_span() -> None:
    assert overlap_ratio(Span("T", 0, 10), Span("T", 0, 10)) == 1.0
    assert overlap_ratio(Span("T", 0, 10), Span("T", 5, 10)) == 0.5
    assert overlap_ratio(Span("T", 0, 10), Span("T", 0, 30)) == pytest.approx(1 / 3)
    assert overlap_ratio(Span("T", 0, 5), Span("T", 5, 9)) == 0.0


def test_match_case_counts_tp_fn_fp_and_confusion() -> None:
    expected = [Span("TR_TCKN", 0, 11), Span("TR_PHONE", 20, 30), Span("TR_VKN", 40, 50)]
    predicted = [Span("TR_TCKN", 0, 11), Span("TR_PHONE", 20, 24), Span("TR_PHONE", 40, 50)]
    result = match_case(expected, predicted)
    assert [e.type for e, _ in result.tp] == ["TR_TCKN"]
    assert [e.type for e in result.fn] == ["TR_PHONE", "TR_VKN"]  # 4/10 overlap < 50 %
    assert [p.type for p in result.fp] == ["TR_PHONE", "TR_PHONE"]
    assert [(e.type, p.type) for e, p in result.confused] == [("TR_VKN", "TR_PHONE")]


def test_one_prediction_matches_one_expected_span() -> None:
    result = match_case([Span("T", 0, 10), Span("T", 0, 10)], [Span("T", 0, 10)])
    assert len(result.tp) == 1 and len(result.fn) == 1 and not result.fp


def test_scores_and_false_alarm_rate() -> None:
    scores = Scores()
    scores.add(["a"], match_case([Span("T", 0, 4)], [Span("T", 0, 4)]), negative=False)
    scores.add(["neg"], match_case([], [Span("T", 0, 4)]), negative=True)
    scores.add(["neg"], match_case([], []), negative=True)
    t = scores.by_type["T"]
    assert (t.tp, t.fp, t.fn) == (1, 1, 0)
    assert t.precision == 0.5 and t.recall == 1.0 and t.f1 == pytest.approx(2 / 3)
    assert scores.false_alarm_rate == 0.5
    assert scores.by_tag["neg"].fp == 1
    assert TypeScore().precision is None and TypeScore().recall is None


# ------------------------------------------------------------------ dataset


def test_committed_dataset_is_the_generator_output() -> None:
    # The JSONL is generated, never hand-edited: regenerate with the module when cases change.
    committed = load_cases(L0_DATASET, "all")
    assert committed == json.loads(json.dumps(generator.build(), ensure_ascii=False))


def test_dataset_invariants() -> None:
    cases = load_cases(L0_DATASET, "all")
    assert len({c["case_id"] for c in cases}) == len(cases)
    assert {c["split"] for c in cases} == {"dev", "test"}
    for case in cases:
        assert case["source"] == "generated" and case["tags"]
        for span in case["expected"]:
            value = case["text"][span["start"] : span["end"]]
            assert value and value == value.strip(), case["case_id"]
    negatives = [c for c in cases if "negative" in c["tags"]]
    assert negatives and all(not c["expected"] for c in negatives)


def test_generated_identifiers_have_valid_checksums() -> None:
    assert all(generator._tckn_valid(v) for v in generator.TCKN)
    assert generator._tckn_valid("10000000146") and not generator._tckn_valid("10000000147")
    assert generator._vkn_check("454053692") == "4540536920"
    assert generator._iban_tr("0006100519786457841326") == "TR330006100519786457841326"
    assert generator._luhn_complete("424242424242424") == "4242424242424242"


def test_naive_baseline_finds_shapes_without_checksums() -> None:
    spans = naive_regex.detect("TC 12345678901 ve kart 4242 4242 4242 4242")
    assert [s.type for s in spans] == ["TR_TCKN", "CREDIT_CARD"]


# ------------------------------------------------------------------ suites (smoke)


@pytest.fixture
def reports(tmp_path, monkeypatch):
    monkeypatch.setattr(evals.common, "REPORTS", tmp_path)
    return tmp_path


def _args(**overrides) -> argparse.Namespace:
    values = {
        "split": "dev",
        "tag": "pytest",
        "bundle": None,
        "bundle_id": None,
        "bundle_version": None,
        "sensitivity": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_l0_suite_writes_a_summary_without_text(reports) -> None:
    directory = l0_pii_tr.run(_args())
    summary = (directory / "summary.md").read_text(encoding="utf-8")
    assert "## Headline" in summary and "| TR_TCKN |" in summary
    texts = [c["text"] for c in load_cases(L0_DATASET, "dev") if c["expected"]]
    results = (directory / "results.jsonl").read_text(encoding="utf-8")
    assert not any(text in summary or text in results for text in texts)


def test_leak_suite_passes(reports) -> None:
    directory = leak.run(_args())
    assert "## Verdict: PASS" in (directory / "summary.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------ S0 measurements


def test_error_paths_reach_every_refusal_without_leaks(detector) -> None:
    """The leak suite's error part (launch plan S0-4) reaches the refusals it claims, so a
    probe that silently lands elsewhere (another status, another code) is noticed."""

    cases = [c for c in load_cases(L0_DATASET, "dev") if c["expected"]]
    probes = leak_errors.run_error_paths(cases, detector)
    assert not [p.label for p in probes if p.leaked]
    reached = {(p.surface, p.status, p.code) for p in probes}
    expected = {
        ("engine", 401, "invalid_credential"),
        ("engine", 403, "tenant_mismatch"),
        ("engine", 403, "insufficient_scope"),
        ("engine", 403, "ip_not_allowed"),
        ("engine", 403, "guardrail_mismatch"),
        ("engine", 413, "input_too_large"),
        ("engine", 422, "invalid_request"),
        ("engine", 422, "bundle_required"),
        ("engine", 429, "rate_limited"),
        ("engine", 429, "quota_exceeded"),
        ("engine", 429, "too_many_failed_attempts"),
        ("gateway", 401, "invalid_credential"),
        ("gateway", 403, "ip_not_allowed"),
        ("gateway", 400, "invalid_request"),
        ("gateway", 400, "umai_blocked"),
        ("gateway", 413, "request_too_large"),
        ("gateway", 429, "quota_exceeded"),
        ("gateway", 502, "upstream_unreachable"),
        ("gateway", 504, "upstream_timeout"),
        ("policy", 422, "invalid_request"),
    }
    assert expected <= reached, sorted(expected - reached)
    assert {p.status for p in probes if "500" in p.label} == {500}


def test_error_probes_count_raw_values_and_content_echoes() -> None:
    values = leak_errors.Values({"TR_TCKN": ["10000000146"], "TR_IBAN": ["TR33 0006 1005"]})
    assert values.found("id 10000000146") == ["TR_TCKN"]
    assert values.found("iban 3300061005") == ["TR_IBAN"]  # its digits are a form too
    assert values.found("0006 1005") == []
    passed = leak_errors.Probe("gateway", "x", 400, "e", response=["TR_TCKN"], passthrough=True)
    own = leak_errors.Probe("gateway", "x", 400, "e", echo=True)
    assert not passed.leaked and own.leaked


def test_load_bundle_enforces_two_policies_through_the_stub() -> None:
    bundle = parse_bundle(stack.LocalStack.l1b_bundle_yaml().encode("utf-8"))
    assert bundle.id == stack.L1B_BUNDLE and bundle.l1b.provider == "stub"
    enforced = {p.id for p in bundle.l1b.policies if p.enforcement == "enforce"}
    assert enforced == set(stack.L1B_ENFORCED)
    assert len(bundle.l1b.policies) > len(enforced)  # the rest run in shadow
    assert bundle.l1b.timeout_ms == stack.L1B_TIMEOUT_MS


def test_stub_provider_answers_every_question_benignly() -> None:
    questions = [
        {"type": "choice", "name": "c", "choices": [{"value": "attack"}, {"value": "general"}]},
        {"type": "score", "name": "s", "levels": [{"label": "none"}, {"label": "high"}]},
        {"type": "predicate", "name": "p"},
    ]
    answers = [stubs._answer(q) for q in questions]
    assert answers[0]["choice"] == "general" and answers[1]["score"] == 0
    assert answers[2]["probability"] < 0.5
    assert [a["name"] for a in answers] == ["c", "s", "p"]


def test_load_texts_are_unique_and_sized() -> None:
    texts = load.Texts(1000)
    batch = [texts.next() for _ in range(500)]
    assert len(set(batch)) == len(batch)  # no L0 cache hit (ADR-0037)
    assert {len(t) for t in batch} == {1000}


def test_load_capacity_ignores_steps_with_degraded_answers() -> None:
    """A saturated step can answer 200 with a failure-policy decision (an L1b call past its
    timeout): that is overload, not capacity."""

    fast, clean = load.Step("evaluate-l1b", "c=32", 10.0), load.Step("evaluate-l1b", "c=4", 10.0)
    for _ in range(600):
        fast.record((200, None, "BLOCK!"), 900.0)
    for _ in range(400):
        clean.record((200, None, "ALLOW"), 40.0)
    assert fast.degraded == 600 and fast.actions == {"BLOCK": 600}
    assert load._best([fast, clean], "evaluate-l1b") is clean
