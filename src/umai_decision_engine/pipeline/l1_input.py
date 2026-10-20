"""What L1b may judge: the parts L0 scanned, every detected entity replaced by its placeholder
(ADR-0005, ADR-0040). Nothing L0 did not scan, and no raw value, reaches a decision model."""

from __future__ import annotations

from umai_decision_engine.contracts.entities import DetectedEntity
from umai_decision_engine.l0.target import SEPARATOR


def redacted_parts(
    parts: list[tuple[str, str]], entities: list[DetectedEntity]
) -> list[tuple[str, str]]:
    """``parts`` are ``(role, text)`` in the order L0 scanned them; ``entities`` carry offsets
    in ``SEPARATOR.join(texts)``."""

    out: list[tuple[str, str]] = []
    offset = 0
    ordered = sorted(entities, key=lambda e: e.start)
    for role, text in parts:
        end = offset + len(text)
        pieces: list[str] = []
        cursor = 0
        for entity in ordered:
            if entity.start < offset or entity.end > end:
                continue
            start, stop = entity.start - offset, entity.end - offset
            if start < cursor:  # L0 resolves overlaps; never expected
                continue
            pieces.append(text[cursor:start])
            pieces.append(entity.placeholder)
            cursor = stop
        pieces.append(text[cursor:])
        out.append((role, "".join(pieces)))
        offset = end + len(SEPARATOR)
    return out
