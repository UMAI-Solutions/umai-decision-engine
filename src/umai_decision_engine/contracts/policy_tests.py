"""Policy golden tests (docs/contracts/policy-tests.md v0.1, ADR-0015)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from umai_decision_engine.contracts.common import Action, ContractModel, Phase, Severity
from umai_decision_engine.contracts.evaluation import (
    AgentAction,
    Artifact,
    ChatMessage,
    Destination,
    Subject,
)

POLICY_TESTS_VERSION = "0.1"


class CaseInput(ContractModel):
    """Evaluation request fields of a case; also the shape of a file's ``defaults``."""

    text: str | None = None
    messages: list[ChatMessage] | None = Field(None, min_length=1)
    focus: Literal["LAST_USER_MESSAGE", "LAST_ASSISTANT_MESSAGE"] | None = None
    artifacts: list[Artifact] | None = None
    language: str | None = Field(None, max_length=8)
    phase: Phase | None = None
    subject: Subject | None = None
    destination: Destination | None = None
    action: AgentAction | None = None

    def merged_over(self, defaults: CaseInput) -> CaseInput:
        """This input with unset fields taken from ``defaults`` (whole fields, no deep merge)."""

        data = defaults.model_dump(exclude_unset=True, by_alias=True)
        data.update(self.model_dump(exclude_unset=True, by_alias=True))
        merged = CaseInput.model_validate(data)
        if (merged.text is None) == (merged.messages is None):
            raise ValueError("input needs exactly one of 'text' or 'messages'")
        return merged


class Expectation(ContractModel):
    action: Action | None = None
    acceptable: list[Action] | None = Field(None, min_length=1)
    decided_by: Literal["L0", "L1a", "L1b", "pipeline"] | None = None
    severity: Severity | None = None
    entities: list[str] | None = None
    masked: list[str] | None = None
    redacted_text: str | None = None
    reason_contains: str | None = None
    would_have: Action | None = None
    errors: list[str] | None = None
    degraded: bool | None = None

    @model_validator(mode="after")
    def _one_decision(self) -> Expectation:
        if (self.action is None) == (self.acceptable is None):
            raise ValueError("expect needs exactly one of 'action' or 'acceptable'")
        return self

    def entity_types(self) -> set[str]:
        return set(self.entities or []) | set(self.masked or [])


class GoldenCase(ContractModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    description: str | None = None
    input: CaseInput
    expect: Expectation


class PolicyTestFile(ContractModel):
    contract_version: Literal["0.1"]
    defaults: CaseInput = Field(default_factory=CaseInput)
    cases: list[GoldenCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _defaults_have_no_content(self) -> PolicyTestFile:
        if self.defaults.text is not None or self.defaults.messages is not None:
            raise ValueError("defaults cannot set 'text' or 'messages'")
        return self
