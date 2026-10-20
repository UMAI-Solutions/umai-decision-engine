"""The decision-model interface (docs/architecture/l1-policy-decision.md, ADR-0004).

Adapters translate ``DecisionRequest`` into a provider's wire format and its answers into
``DecisionResult``. They never log request or response bodies, never retry (the pipeline
decides on a fallback) and take their timeout from the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from umai_decision_engine.contracts.questions import DecisionRequest, DecisionResult

ErrorKind = Literal["timeout", "rate_limited", "unavailable", "invalid_response", "auth"]


class DecisionModelError(Exception):
    """A provider call that produced no usable answers; ``kind`` names the failure."""

    def __init__(self, kind: ErrorKind, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind: ErrorKind = kind


@dataclass(frozen=True)
class ProviderCapabilities:
    probabilities: bool
    question_types: frozenset[str]
    max_questions: int
    max_input_chars: int


class DecisionModel(Protocol):
    name: str
    adapter: str
    capabilities: ProviderCapabilities

    async def decide(self, request: DecisionRequest, *, timeout_s: float) -> DecisionResult: ...


def winning_confidence(probabilities: dict[str, float] | list[float] | None) -> float | None:
    """Our provider-independent confidence: the winning option's probability."""

    if not probabilities:
        return None
    values = probabilities.values() if isinstance(probabilities, dict) else probabilities
    return max(values)
