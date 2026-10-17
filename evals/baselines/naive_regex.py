"""Naive regex baseline: digit-shape patterns without checksums or context, the way a typical
regex PII rule library is written. Illustrative only — it is NOT umai-engine's rule set,
which is the baseline named in the F1 exit criterion (docs/roadmap.md)."""

from __future__ import annotations

import re

from evals.metrics import Span

NAME = "naive-regex"

_PATTERNS: dict[str, re.Pattern[str]] = {
    "TR_TCKN": re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)"),
    "TR_IBAN": re.compile(r"\bTR\d{2}(?: ?\d{4}){5} ?\d{2}\b"),
    "IBAN_CODE": re.compile(r"\b(?!TR)[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    "TR_PHONE": re.compile(r"(?<!\d)(?:\+90|0)? ?\(?5\d{2}\)? ?\d{3} ?\d{2} ?\d{2}(?!\d)"),
    "CREDIT_CARD": re.compile(r"(?<!\d)(?:\d{4}[ -]?){3}\d{4}(?!\d)"),
    "EMAIL_ADDRESS": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
}


def detect(text: str) -> list[Span]:
    spans = [
        Span(entity_type, m.start(), m.end())
        for entity_type, pattern in _PATTERNS.items()
        for m in pattern.finditer(text)
    ]
    # drop shorter spans contained in a longer one (e.g. TCKN digits inside an IBAN)
    spans.sort(key=lambda s: (s.start, -(s.end - s.start)))
    kept: list[Span] = []
    for span in spans:
        if not any(k.start <= span.start and span.end <= k.end for k in kept):
            kept.append(span)
    return kept
