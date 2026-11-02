"""Guardrails contract (docs/contracts/guardrails.md, ADR-0056): creating a guardrail from a
template or as a copy, and the template listing. Drafts and test reports are the policy
service's dicts, described in the contract document."""

from __future__ import annotations

from pydantic import Field, model_validator

from umai_decision_engine.contracts.common import ContractModel

CONTRACT_VERSION = "0.1"

BUNDLE_ID = r"^[a-z0-9][a-z0-9._\-]{0,127}$"  # policy-bundle `id`


class GuardrailSource(ContractModel):
    """Exactly one of ``template`` and ``guardrail``; ``version`` only with ``guardrail``
    (default: its active version)."""

    template: str | None = Field(None, pattern=BUNDLE_ID)
    guardrail: str | None = Field(None, pattern=BUNDLE_ID)
    version: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def _one(self) -> GuardrailSource:
        if (self.template is None) == (self.guardrail is None):
            raise ValueError("name either a template or a guardrail")
        if self.template is not None and self.version is not None:
            raise ValueError("version: only with guardrail; a template is copied as it is active")
        return self

    def as_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


class GuardrailCreate(ContractModel):
    """``POST /v1/guardrails``."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1_000)
    id: str | None = Field(None, pattern=BUNDLE_ID)
    from_: GuardrailSource = Field(alias="from")


class GuardrailArchive(ContractModel):
    """``POST /v1/guardrails/{id}/archive``."""

    note: str | None = Field(None, max_length=500)


class Template(ContractModel):
    """One entry of ``GET /v1/guardrail-templates``."""

    id: str = Field(pattern=BUNDLE_ID)
    version: int = Field(ge=1)
    name: str
    description: str | None = None
    l0_policies: list[str]
    decision_policies: list[str]
    cases: int = Field(ge=0)
