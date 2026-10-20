"""Prompt-attack detectors: text in, attack probability out (gateway WP4, ADR-0036).

A detector only classifies; whether a score blocks is the bundle's ``prompt_attacks`` policy,
applied by deterministic code (AGENTS rule 3). Detectors receive redacted text (ADR-0005).
"""

from __future__ import annotations

from typing import Protocol


class AttackDetector(Protocol):
    name: str

    def scores(self, texts: list[str]) -> list[float]:
        """One probability in [0, 1] per text that it is a prompt attack."""
        ...


class MaxDetector:
    """Several detectors as one: a text's score is the highest of theirs."""

    def __init__(self, detectors: list[AttackDetector]) -> None:
        if not detectors:
            raise ValueError("at least one detector")
        self.detectors = detectors
        self.name = "+".join(d.name for d in detectors)

    def scores(self, texts: list[str]) -> list[float]:
        columns = [d.scores(texts) for d in self.detectors]
        return [max(values) for values in zip(*columns, strict=True)]
