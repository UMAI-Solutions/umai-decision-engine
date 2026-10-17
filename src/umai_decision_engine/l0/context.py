"""Turkish-aware context scoring (replaces Presidio's context enhancer for L0).

Presidio's enhancer breaks on Turkish (case folding, multi-word entries, suffixes; and the
``no_op`` engine has no tokens). Rules (docs/architecture/l0-entity-detection.md):
- window of N words before and M words after the match;
- Turkish-aware folding on the window only (offsets never change);
- prefix matching for terms of 4+ letters (``kimlik`` matches ``kimliğim``), exact match
  for short terms (``tc``, ``tel``); phrases match adjacent words.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

import regex

from umai_decision_engine.l0.normalize import match_fold

_WORD = regex.compile(r"\w+")
WORDS_BEFORE = 6
WORDS_AFTER = 3
_PREFIX_MIN_LEN = 4


@lru_cache(maxsize=512)
def _term_words(term: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(match_fold(term)))


@lru_cache(maxsize=256)
def _matcher(terms: tuple[str, ...]) -> regex.Pattern[str] | None:
    """One regex per term list over the space-joined window words: words of 4+ letters match
    as prefixes (``kimlik`` → ``kimliğim``), shorter ones exactly; phrases match adjacent words."""

    alternatives = []
    for term in terms:
        words = _term_words(term)
        if words:
            alternatives.append(
                " ".join(
                    regex.escape(w) + (r"\w*" if len(w) >= _PREFIX_MIN_LEN else "") for w in words
                )
            )
    if not alternatives:
        return None
    return regex.compile(r"(?:^| )(?:" + "|".join(alternatives) + r")(?= |$)")


@lru_cache(maxsize=8192)
def _window(text: str, start: int, end: int) -> str:
    before = _WORD.findall(match_fold(text[max(0, start - 200) : start]))[-WORDS_BEFORE:]
    after = _WORD.findall(match_fold(text[end : end + 120]))[:WORDS_AFTER]
    return " ".join([*before, *after])


def context_words(text: str, start: int, end: int) -> list[str]:
    return _window(text, start, end).split()


def has_context(text: str, start: int, end: int, terms: Sequence[str]) -> bool:
    if not terms:
        return False
    matcher = _matcher(tuple(terms))
    return matcher is not None and matcher.search(_window(text, start, end)) is not None
