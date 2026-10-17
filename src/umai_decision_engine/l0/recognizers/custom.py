"""Customer-defined recognizers from the bundle (``l0.custom_recognizers``, policy-bundle 0.5).

A guardrail can add its own entity types (customer numbers, internal project codes, …) as
regex patterns with context words and an optional built-in validator, the way Lakera's custom
data-leakage guardrails work (docs/reference/lakera-guard.md). They run through the same
``SpecRecognizer`` as built-in types; compiled specs are cached per definition.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

import regex

from umai_decision_engine.contracts.bundle import CustomRecognizer
from umai_decision_engine.l0.recognizers.base import (
    Candidate,
    PatternSpec,
    RecognizerSpec,
    SpecRecognizer,
)
from umai_decision_engine.l0.validators import (
    de_tax_id_is_valid,
    digits_only,
    es_nie_is_valid,
    es_nif_is_valid,
    fr_nir_is_valid,
    iban_mod97_is_valid,
    it_fiscal_code_is_valid,
    luhn_is_valid,
    nl_bsn_is_valid,
    pl_pesel_is_valid,
    tckn_is_valid,
    uk_nino_is_valid,
    us_ssn_is_valid,
    vkn_is_valid,
)


def _alnum_upper(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum()).upper()


# Built-in checksum functions a custom recognizer may name in ``validator``.
VALIDATORS: dict[str, tuple[Callable[[str], bool], Callable[[str], str]]] = {
    "luhn": (luhn_is_valid, digits_only),
    "iban_mod97": (iban_mod97_is_valid, _alnum_upper),
    "tckn": (tckn_is_valid, digits_only),
    "vkn": (vkn_is_valid, digits_only),
    "us_ssn": (us_ssn_is_valid, digits_only),
    "uk_nino": (uk_nino_is_valid, _alnum_upper),
    "de_tax_id": (de_tax_id_is_valid, digits_only),
    "fr_nir": (fr_nir_is_valid, _alnum_upper),
    "es_nif": (es_nif_is_valid, _alnum_upper),
    "es_nie": (es_nie_is_valid, _alnum_upper),
    "it_fiscal_code": (it_fiscal_code_is_valid, _alnum_upper),
    "nl_bsn": (nl_bsn_is_valid, digits_only),
    "pl_pesel": (pl_pesel_is_valid, digits_only),
}

# Nested unbounded quantifiers ("(a+)+", "(.*)*", "(\w+\s?)*") are the classic catastrophic
# backtracking shapes; reject them at bundle load. The matcher also has a timeout.
_NESTED_QUANTIFIER = regex.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*(?:[+*]|\{\d*,\})")


def pattern_problems(pattern: str) -> list[str]:
    problems: list[str] = []
    try:
        regex.compile(pattern)
    except regex.error as exc:
        problems.append(f"does not compile ({exc})")
        return problems
    if _NESTED_QUANTIFIER.search(pattern):
        problems.append("nested unbounded quantifier (catastrophic backtracking risk)")
    if regex.compile(pattern).match(""):
        problems.append("matches the empty string")
    return problems


def _key(recognizer: CustomRecognizer) -> tuple:
    return (
        recognizer.entity,
        tuple((p.name, p.regex, p.score) for p in recognizer.patterns),
        tuple(recognizer.context),
        recognizer.validator,
    )


@lru_cache(maxsize=256)
def _spec(key: tuple) -> RecognizerSpec:
    entity, patterns, context, validator = key
    refine = None
    canonical: Callable[[str], str] = lambda value: value.strip()  # noqa: E731
    if validator is not None:
        check, canonical = VALIDATORS[validator]

        def refine(fragment: str, _check=check, _canonical=canonical) -> list[Candidate]:
            return [Candidate(0, len(fragment), True)] if _check(_canonical(fragment)) else []

    return RecognizerSpec(
        entity=entity,
        patterns=tuple(PatternSpec(name, rx, score) for name, rx, score in patterns),
        canonical=canonical,
        refine=refine,
        context=context,
        context_boost=0.25,
        priority=4,
    )


@lru_cache(maxsize=512)
def _recognizer(key: tuple, language: str) -> SpecRecognizer:
    return SpecRecognizer(_spec(key), supported_language=language)


def custom_recognizers(definitions: list[CustomRecognizer], language: str) -> list[SpecRecognizer]:
    return [_recognizer(_key(d), language) for d in definitions]
