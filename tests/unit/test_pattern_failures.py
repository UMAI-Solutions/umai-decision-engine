"""A pattern that times out or fails raises out of L0, never skipped (AGENTS.md rule 4;
ADR-0056 build step E1). Synthetic data only."""

from __future__ import annotations

import dataclasses
import logging

import pytest

from tests.conftest import CATASTROPHIC_PATTERNS
from umai_decision_engine.contracts.bundle import CustomPattern, CustomRecognizer, L0Config
from umai_decision_engine.l0 import detector as detector_module
from umai_decision_engine.l0.recognizers import base
from umai_decision_engine.l0.recognizers.base import PatternMatchError
from umai_decision_engine.l0.recognizers.catalog import SPECS_BY_ENTITY
from umai_decision_engine.l0.recognizers.custom import custom_recognizers, pattern_problems

CANARY = "kanarya"  # must never reach an exception or a log line
ATTACK = CANARY + "1" * 16_000 + "!"  # "almost matches": exponential backtracking
TCKN = "10000000146"  # synthetic test vector (docs/reference/turkish-identifiers.md)


@pytest.fixture(autouse=True)
def fast_timeout(monkeypatch) -> None:
    # Read from REGEX_TIMEOUT_SECONDS at import (default 1 s); patched to keep the tests fast.
    monkeypatch.setattr(base, "_REGEX_TIMEOUT_S", 0.05)


@pytest.fixture(autouse=True)
def debug_logs(caplog) -> None:
    # Every line, whatever level an earlier test left on "umai.de" (the golden CLI sets ERROR).
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="umai.de")


def _custom(pattern: str) -> CustomRecognizer:
    return CustomRecognizer(
        entity="CUSTOMER_NO", patterns=[CustomPattern(name="greedy", regex=pattern, score=0.9)]
    )


def _assert_no_content(exc: BaseException) -> None:
    assert exc.__cause__ is None and exc.__context__ is None  # nothing chained that quotes text
    for rendered in (str(exc), repr(exc)):
        assert CANARY not in rendered and TCKN not in rendered


@pytest.mark.parametrize("pattern", CATASTROPHIC_PATTERNS)
def test_a_timeout_raises_instead_of_skipping_the_pattern(pattern, caplog) -> None:
    assert pattern_problems(pattern) == []  # the bundle-load check lets it through
    (recognizer,) = custom_recognizers([_custom(pattern)], "tr")
    with pytest.raises(PatternMatchError) as raised:
        recognizer.analyze(ATTACK, ["CUSTOMER_NO"])
    error = raised.value
    assert (error.recognizer, error.entity, error.pattern, error.reason) == (
        "umai_customer_no",
        "CUSTOMER_NO",
        "greedy",
        "timeout",
    )
    assert error.elapsed_ms >= 40
    _assert_no_content(error)
    assert (
        "l0.pattern_failed recognizer=umai_customer_no entity=CUSTOMER_NO pattern=greedy "
        "reason=timeout elapsed_ms="
    ) in caplog.text
    assert CANARY not in caplog.text


def test_the_detector_raises_for_a_custom_recognizer(detector) -> None:
    config = L0Config(entities=["CUSTOMER_NO"], custom_recognizers=[_custom(r"(\w|\d)+$")])
    with pytest.raises(PatternMatchError) as raised:
        detector.detect(ATTACK, tenant_id="t", config=config)
    _assert_no_content(raised.value)


def test_any_other_matcher_error_raises_without_its_message(monkeypatch, caplog) -> None:
    # A built-in spec run through Presidio as the detector builds it: Presidio does not swallow
    # recognizer exceptions, and the cause's message (which quotes the value) is dropped.
    def broken_refine(fragment: str) -> list:
        raise ValueError(f"cannot validate {fragment}")

    spec = dataclasses.replace(SPECS_BY_ENTITY["TR_TCKN"], refine=broken_refine)
    monkeypatch.setattr(detector_module, "ALL_SPECS", (spec,))
    engine = detector_module.build_analyzer()
    with pytest.raises(PatternMatchError) as raised:
        engine.analyze(
            text=f"TC kimlik {TCKN}", language="tr", entities=["TR_TCKN"], score_threshold=0.0
        )
    assert raised.value.recognizer == spec.recognizer_name
    assert raised.value.reason == "ValueError"
    _assert_no_content(raised.value)
    assert "l0.pattern_failed" in caplog.text and "reason=ValueError" in caplog.text
    assert TCKN not in caplog.text


def test_ordinary_patterns_still_match() -> None:
    (recognizer,) = custom_recognizers([_custom(r"\bMUS-[0-9]{6}\b")], "tr")
    results = recognizer.analyze("Müşteri no MUS-123456 iptal", ["CUSTOMER_NO"])
    assert [(r.start, r.end) for r in results] == [(11, 21)]
