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


def _word_matches(word: str, term_word: str) -> bool:
    if len(term_word) >= _PREFIX_MIN_LEN:
        return word.startswith(term_word)
    return word == term_word


def _contains_term(words: Sequence[str], term: tuple[str, ...]) -> bool:
    if not term:
        return False
    span = len(term)
    for i in range(len(words) - span + 1):
        if all(_word_matches(words[i + k], term[k]) for k in range(span)):
            return True
    return False


def context_words(text: str, start: int, end: int) -> list[str]:
    before = _WORD.findall(match_fold(text[max(0, start - 200) : start]))[-WORDS_BEFORE:]
    after = _WORD.findall(match_fold(text[end : end + 120]))[:WORDS_AFTER]
    return [*before, *after]


def has_context(text: str, start: int, end: int, terms: Sequence[str]) -> bool:
    if not terms:
        return False
    words = context_words(text, start, end)
    return any(_contains_term(words, _term_words(term)) for term in terms)
