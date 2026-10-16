"""Text normalization for matching, with an offset map back to the original text.

Steps (docs/architecture/l0-entity-detection.md, step 1):
- drop zero-width and bidi control characters (an evasion technique);
- NFKC per base character + its combining marks, so full-width digits become ASCII and
  NFD sequences (``s`` + U+0327) compose to ``ş``.

Every normalized character remembers the original span it came from, so entity offsets
can be reported against the original text.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

_INVISIBLE: frozenset[int] = frozenset(
    [*range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2060, 0x2065), 0xFEFF, 0x00AD]
)


@dataclass(frozen=True)
class NormalizedText:
    text: str
    _starts: tuple[int, ...]
    _ends: tuple[int, ...]

    def to_original(self, start: int, end: int) -> tuple[int, int]:
        """Map a ``[start, end)`` span of the normalized text to the original text."""

        if start >= end:
            raise ValueError("empty span")
        return self._starts[start], self._ends[end - 1]


def normalize(original: str) -> NormalizedText:
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    i = 0
    n = len(original)
    while i < n:
        if ord(original[i]) in _INVISIBLE:
            i += 1
            continue
        j = i + 1
        while j < n and unicodedata.combining(original[j]):
            j += 1
        segment = unicodedata.normalize("NFKC", original[i:j])
        for ch in segment:
            if ord(ch) in _INVISIBLE:
                continue
            out.append(ch)
            starts.append(i)
            ends.append(j)
        i = j
    return NormalizedText("".join(out), tuple(starts), tuple(ends))


def tr_casefold(value: str) -> str:
    """Turkish-aware lowercase: ``İ`` → ``i``, ``I`` → ``ı``; drops the combining dot.

    ``str.lower()`` maps ``İ`` to ``i`` + U+0307 and ``I`` to ``i``, which breaks Turkish
    context words written in capitals (``KİMLİK``).
    """

    return value.replace("İ", "i").replace("I", "ı").lower().replace("̇", "")


def match_fold(value: str) -> str:
    """Fold for keyword matching across Turkish and English: also merges ``ı`` into ``i``.

    Lossy on purpose: ``KİMLİK`` → ``kimlik`` and ``IBAN`` → ``iban`` both match.
    """

    return tr_casefold(value).replace("ı", "i")
