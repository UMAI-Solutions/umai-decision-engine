"""Decision questions: L1b → provider adapter and back (docs/contracts/decision-questions.md).

Provider-agnostic: adapters compile ``DecisionRequest`` to their wire format and parse their
answers into ``DecisionResult``. Nothing here carries raw content: the judged content is L0's
redacted text (ADR-0005), and no user identifiers are sent (role, department and destination
class only).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from umai_decision_engine.contracts.common import ContractModel

CONTRACT_VERSION = "0.4"


class QuestionOption(ContractModel):
    value: str
    description: str | None = None


class QuestionLevel(ContractModel):
    label: str
    description: str | None = None


class DecisionQuestion(ContractModel):
    """One question, namespaced ``<policy id>.<question id>``."""

    qid: str = Field(min_length=3)
    type: Literal["condition", "choice", "score"]
    instructions: str = Field(min_length=1)
    options: list[QuestionOption] | None = None  # choice
    levels: list[QuestionLevel] | None = None  # score
    criteria: dict[str, str] | None = None  # condition: what true / false mean


class ExcerptMessage(ContractModel):
    """One part of the judged content: L0-redacted text with its role."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class DecisionRequest(ContractModel):
    request_id: str
    policies: list[dict[str, Any]] = Field(default_factory=list)  # {id, version, policy_text}
    facts: dict[str, Any] = Field(default_factory=dict)  # phase, destination, subject, entities
    # The judged content, oldest first; the last message is the one in focus. Redacted only.
    excerpt: list[ExcerptMessage] = Field(min_length=1)
    questions: list[DecisionQuestion] = Field(min_length=1)


class Answer(ContractModel):
    """A provider's answer to one question. ``refused``: no answer (not an error)."""

    refused: bool = False
    value: str | None = None  # choice
    probabilities: dict[str, float] | list[float] | None = None  # choice: by value; score: by level
    confidence: float | None = None  # winning option's probability (choice, score)
    probability: float | None = None  # condition: probability of "true"
    level: int | None = None  # score: argmax level
    expected: float | None = None  # score: probability-weighted level


class DecisionResult(ContractModel):
    provider: str
    adapter: str
    model: str | None = None
    latency_ms: float
    answers: dict[str, Answer]
    usage: dict[str, int] = Field(default_factory=dict)  # token counts only
