"""Deterministic decision model for tests, golden cases and offline development.

Answers come from a lookup keyed by request id, then by question id; a question without an
answer comes back ``refused`` (the model said nothing), so ``decide`` falls through to its
default. Short answer forms: a choice value (``"injection_detected"``), a condition
probability (``0.93``), a score level (``{"level": 2}``) or a full ``Answer``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from umai_decision_engine.contracts.questions import (
    Answer,
    DecisionQuestion,
    DecisionRequest,
    DecisionResult,
)
from umai_decision_engine.l1.decision_models.base import (
    DecisionModelError,
    ProviderCapabilities,
    winning_confidence,
)

AnswerSource = Callable[[DecisionRequest], dict[str, Any]]


def answer_from(question: DecisionQuestion, short: Any) -> Answer:
    """A full ``Answer`` from a short form (see the module docstring)."""

    if isinstance(short, Answer):
        return short
    if isinstance(short, dict) and "refused" in short:
        return Answer.model_validate(short)
    if question.type == "choice":
        if isinstance(short, dict):
            value = short["value"]
            probabilities = short.get("probabilities")
            if probabilities is None and "p" in short:
                rest = [o.value for o in question.options or [] if o.value != value]
                share = (1 - short["p"]) / len(rest) if rest else 0.0
                probabilities = {value: short["p"], **{r: share for r in rest}}
        else:
            value, probabilities = short, None
        return Answer(
            value=value,
            probabilities=probabilities,
            confidence=winning_confidence(probabilities),
        )
    if question.type == "condition":
        return Answer(probability=float(short))
    level = short["level"] if isinstance(short, dict) else int(short)
    return Answer(level=level, expected=float(level))


class FakeDecisionModel:
    adapter = "fake"
    capabilities = ProviderCapabilities(
        probabilities=True,
        question_types=frozenset({"condition", "choice", "score"}),
        max_questions=200,
        max_input_chars=1_000_000,
    )

    def __init__(
        self,
        name: str = "fake",
        answers: AnswerSource | dict[str, Any] | None = None,
        *,
        error: str | None = None,
        latency_s: float = 0.0,
    ) -> None:
        self.name = name
        self._answers = answers
        self.error = error
        self.latency_s = latency_s
        self.requests: list[DecisionRequest] = []
        self.warm_ups = 0

    async def warm_up(self, *, timeout_s: float) -> str:
        self.warm_ups += 1
        return "none"

    async def decide(self, request: DecisionRequest, *, timeout_s: float) -> DecisionResult:
        import asyncio

        self.requests.append(request)
        started = time.perf_counter()
        if self.latency_s:
            if self.latency_s >= timeout_s:
                await asyncio.sleep(timeout_s)
                raise DecisionModelError("timeout", "fake provider timed out")
            await asyncio.sleep(self.latency_s)
        if self.error is not None:
            raise DecisionModelError(self.error, "fake provider error")  # type: ignore[arg-type]
        source = self._answers
        given = source(request) if callable(source) else (source or {})
        answers = {
            q.qid: answer_from(q, given[q.qid]) if q.qid in given else Answer(refused=True)
            for q in request.questions
        }
        return DecisionResult(
            provider=self.name,
            adapter=self.adapter,
            model="fake",
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            answers=answers,
        )
