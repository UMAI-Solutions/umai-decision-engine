"""Recognizer specification and the Presidio recognizer that runs it.

We run our own matching inside a Presidio ``LocalRecognizer`` instead of using
``PatternRecognizer``: a regex match can be refined to a valid sub-span (e.g. an IBAN
followed by more capital letters), and validation keeps our own scores instead of
Presidio's "valid → 1.0" rule (docs/reference/presidio.md).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import regex
from presidio_analyzer import LocalRecognizer, RecognizerResult

from umai_decision_engine.l0.context import has_context

# Presidio reads REGEX_TIMEOUT_SECONDS as an int; we use the same variable for our matching.
_REGEX_TIMEOUT_S = float(os.getenv("REGEX_TIMEOUT_SECONDS", "1") or 1)

META_VALIDATED = "umai_validated"
META_PATTERN = "umai_pattern"


@dataclass(frozen=True)
class Candidate:
    """A refined match inside a regex match: offsets relative to the match start."""

    start: int
    end: int
    validated: bool | None
    # Overrides the pattern score (e.g. a checksum failure lowers it, a mobile range raises it).
    score: float | None = None


@dataclass(frozen=True)
class PatternSpec:
    name: str
    regex: str
    score: float
    # Matches without one of the spec's context words are dropped before validation: for
    # shapes that collide with ordinary numbers (VKN, BSN, national-format phones, …) and are
    # never reported without context, this also saves the cost of validating them.
    requires_context: bool = False


@dataclass(frozen=True)
class RecognizerSpec:
    """Everything L0 needs to know about one entity type."""

    entity: str
    patterns: Sequence[PatternSpec]
    canonical: Callable[[str], str]
    # Returns accepted sub-spans of a regex match; default: whole match, no validator.
    refine: Callable[[str], list[Candidate]] | None = None
    context: Sequence[str] = ()
    context_boost: float = 0.25
    # Canonical values that are known not to be personal data (placeholders, examples).
    known_non_pii: frozenset[str] = frozenset()
    priority: int = 1
    flags: int = field(default=regex.MULTILINE)


class SpecRecognizer(LocalRecognizer):
    """Presidio recognizer executing a :class:`RecognizerSpec`."""

    def __init__(self, spec: RecognizerSpec, supported_language: str) -> None:
        self.spec = spec
        self._compiled = [(p, regex.compile(p.regex, flags=spec.flags)) for p in spec.patterns]
        super().__init__(
            supported_entities=[spec.entity],
            name=f"umai_{spec.entity.lower()}",
            supported_language=supported_language,
            context=None,  # context scoring is ours (l0/context.py)
        )

    def load(self) -> None:  # patterns are compiled in __init__
        return None

    def analyze(self, text: str, entities: list[str], nlp_artifacts=None) -> list[RecognizerResult]:
        if entities and self.spec.entity not in entities:
            return []
        results: list[RecognizerResult] = []
        seen: set[tuple[int, int]] = set()
        for pattern, compiled in self._compiled:
            try:
                matches = list(compiled.finditer(text, timeout=_REGEX_TIMEOUT_S))
            except TimeoutError:
                continue
            for match in matches:
                m_start, m_end = match.span()
                if pattern.requires_context and not has_context(
                    text, m_start, m_end, self.spec.context
                ):
                    continue
                fragment = text[m_start:m_end]
                candidates = (
                    self.spec.refine(fragment)
                    if self.spec.refine
                    else [Candidate(0, len(fragment), None)]
                )
                for cand in candidates:
                    start, end = m_start + cand.start, m_start + cand.end
                    if (start, end) in seen or end <= start:
                        continue
                    if self.spec.canonical(text[start:end]) in self.spec.known_non_pii:
                        continue
                    seen.add((start, end))
                    results.append(
                        RecognizerResult(
                            entity_type=self.spec.entity,
                            start=start,
                            end=end,
                            score=pattern.score if cand.score is None else cand.score,
                            recognition_metadata={
                                RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                                RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                                META_VALIDATED: cand.validated,
                                META_PATTERN: pattern.name,
                            },
                        )
                    )
        return results
