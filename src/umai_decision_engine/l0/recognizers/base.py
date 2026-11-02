"""Recognizer specification and the Presidio recognizer that runs it.

We run our own matching inside a Presidio ``LocalRecognizer`` instead of using
``PatternRecognizer``: a regex match can be refined to a valid sub-span (e.g. an IBAN
followed by more capital letters), and validation keeps our own scores instead of
Presidio's "valid → 1.0" rule (docs/reference/presidio.md).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import regex
from presidio_analyzer import LocalRecognizer, RecognizerResult

from umai_decision_engine.l0.context import has_context

logger = logging.getLogger("umai.de.l0")

# Presidio reads REGEX_TIMEOUT_SECONDS as an int; we use the same variable for our matching.
_REGEX_TIMEOUT_S = float(os.getenv("REGEX_TIMEOUT_SECONDS", "1") or 1)

META_VALIDATED = "umai_validated"
META_PATTERN = "umai_pattern"


class PatternMatchError(RuntimeError):
    """A pattern's matching timed out or failed. Raised out of L0, never skipped (AGENTS.md
    rule 4): the evaluation fails with ``L0_ERROR`` under the guardrail's
    ``on_evaluation_failure``. Names and timing only, never content (ADR-0005)."""

    def __init__(
        self, *, recognizer: str, entity: str, pattern: str, reason: str, elapsed_ms: float
    ) -> None:
        self.recognizer = recognizer
        self.entity = entity
        self.pattern = pattern
        self.reason = reason  # "timeout" or the failing exception's class name
        self.elapsed_ms = elapsed_ms
        super().__init__(
            f"pattern {pattern} of recognizer {recognizer} ({entity}) failed: {reason} "
            f"after {elapsed_ms} ms"
        )


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
    # Recognizer name; distinct when one entity type has several specs (their context words
    # and priorities differ). Default: umai_<entity>.
    name: str | None = None

    @property
    def recognizer_name(self) -> str:
        return self.name or f"umai_{self.entity.lower()}"


class SpecRecognizer(LocalRecognizer):
    """Presidio recognizer executing a :class:`RecognizerSpec`."""

    def __init__(self, spec: RecognizerSpec, supported_language: str) -> None:
        self.spec = spec
        self._compiled = [(p, regex.compile(p.regex, flags=spec.flags)) for p in spec.patterns]
        super().__init__(
            supported_entities=[spec.entity],
            name=spec.recognizer_name,
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
            started = time.perf_counter()
            reason: str | None = None
            try:
                self._match(text, pattern, compiled, results, seen)
            except TimeoutError:
                reason = "timeout"
            except Exception as exc:
                reason = type(exc).__name__
            if reason is not None:
                # Raised outside the handlers, so the cause (whose message may quote the text)
                # is not chained to it.
                raise self._failure(pattern, reason, started)
        return results

    def _match(
        self,
        text: str,
        pattern: PatternSpec,
        compiled: regex.Pattern,
        results: list[RecognizerResult],
        seen: set[tuple[int, int]],
    ) -> None:
        for match in list(compiled.finditer(text, timeout=_REGEX_TIMEOUT_S)):
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

    def _failure(self, pattern: PatternSpec, reason: str, started: float) -> PatternMatchError:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        logger.warning(
            "l0.pattern_failed recognizer=%s entity=%s pattern=%s reason=%s elapsed_ms=%s",
            self.name,
            self.spec.entity,
            pattern.name,
            reason,
            elapsed_ms,
        )
        return PatternMatchError(
            recognizer=self.name,
            entity=self.spec.entity,
            pattern=pattern.name,
            reason=reason,
            elapsed_ms=elapsed_ms,
        )
