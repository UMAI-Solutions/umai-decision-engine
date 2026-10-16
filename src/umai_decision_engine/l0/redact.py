"""Placeholder redaction: ``<TYPE_n>`` numbered per type by first appearance."""

from __future__ import annotations

from collections.abc import Iterable

from umai_decision_engine.contracts.entities import DetectedEntity


def apply_redaction(text: str, spans: Iterable[tuple[int, int, str]]) -> str:
    """Replace non-overlapping ``(start, end, placeholder)`` spans of ``text``."""

    out: list[str] = []
    cursor = 0
    for start, end, placeholder in sorted(spans):
        if start < cursor:
            raise ValueError("overlapping redaction spans")
        out.append(text[cursor:start])
        out.append(placeholder)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


class PlaceholderAllocator:
    """Same (type, value hash) → same placeholder within one evaluation."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}

    def placeholder(self, entity_type: str, value_hash: str) -> str:
        key = (entity_type, value_hash)
        if key not in self._by_key:
            n = self._counters.get(entity_type, 0) + 1
            self._counters[entity_type] = n
            self._by_key[key] = f"<{entity_type}_{n}>"
        return self._by_key[key]


def redact_types(text: str, entities: Iterable[DetectedEntity], types: set[str]) -> str:
    """``text`` with only the given entity types replaced by their placeholders.

    Used for ``ALLOW_WITH_MODIFICATIONS``: types an exception allowed stay as written. Lives in
    ``l0`` because it handles raw text (ADR-0005).
    """

    return apply_redaction(
        text, [(e.start, e.end, e.placeholder) for e in entities if e.type in types]
    )
