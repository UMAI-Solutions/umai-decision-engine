"""Policy bundle (docs/contracts/policy-bundle.md v0.3, decision-questions.md v0.3)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from umai_decision_engine.contracts.common import (
    Action,
    ContractModel,
    FailurePolicy,
    Mode,
    Phase,
    Severity,
)

TargetScope = Literal["LAST_MESSAGE", "FULL_HISTORY", "ATTACHMENTS", "FULL_CONTEXT"]
_SNAKE = r"^[a-z][a-z0-9_]*$"
_ENTITY = r"^[A-Z][A-Z0-9_]*$"

# ------------------------------------------------------------------ L0


class NerConfig(ContractModel):
    enabled: bool = False
    model: str | None = None


class CustomPattern(ContractModel):
    name: str
    regex: str
    score: float = Field(ge=0, le=1)


class CustomRecognizer(ContractModel):
    entity: str = Field(pattern=_ENTITY)
    patterns: list[CustomPattern] = Field(min_length=1)
    context: list[str] = Field(default_factory=list)
    validator: str | None = None


# ------------------------------------------------------------------ L1a

Operator = Literal[
    "EQUALS",
    "NOT_EQUALS",
    "IN",
    "NOT_IN",
    "CONTAINS",
    "CONTAINS_ANY",
    "MATCHES_REGEX",
    "EXISTS",
    "NOT_EXISTS",
    "STARTS_WITH",
    "ENDS_WITH",
    "GT",
    "GTE",
    "LT",
    "LTE",
]


class Condition(ContractModel):
    field: str = Field(min_length=1)
    operator: Operator = "EQUALS"
    value: Any = None

    @model_validator(mode="after")
    def _value_required(self) -> Condition:
        if self.operator not in {"EXISTS", "NOT_EXISTS"} and self.value is None:
            raise ValueError(f"value is required for operator {self.operator}")
        return self


class ConditionGroup(ContractModel):
    all_: list[Condition] | None = Field(None, alias="all")
    any_: list[Condition] | None = Field(None, alias="any")

    @model_validator(mode="after")
    def _exactly_one(self) -> ConditionGroup:
        if (self.all_ is None) == (self.any_ is None):
            raise ValueError("a condition group needs exactly one of 'all' or 'any'")
        return self


# ------------------------------------------------------------------ L0 gate (ADR-0014)

# What a detected entity type leads to. ALLOW / ALLOW_WITH_WARNINGS are deliberately absent:
# detected PII never passes unmasked unless an explicit deterministic exception says so.
DetectAction = Literal["BLOCK", "STEP_UP_APPROVAL", "ALLOW_WITH_MODIFICATIONS"]
ExceptionAction = Literal["ALLOW", "ALLOW_WITH_WARNINGS", "ALLOW_WITH_MODIFICATIONS"]


class OnDetect(ContractModel):
    default: DetectAction = "BLOCK"
    severity: Severity = Severity.HIGH
    actions: dict[str, DetectAction] = Field(default_factory=dict)

    def action_for(self, entity_type: str) -> Action:
        return Action(self.actions.get(entity_type, self.default))


class L0Exception(ContractModel):
    id: str = Field(min_length=1)
    description: str | None = None
    when: ConditionGroup
    entities: list[str] = Field(default_factory=list)  # empty → every detected type
    action: ExceptionAction = "ALLOW"

    def covers(self, entity_type: str) -> bool:
        return not self.entities or entity_type in self.entities


class L0Config(ContractModel):
    """Detection settings plus the L0 gate: a detection is terminal (ADR-0014)."""

    target: TargetScope = "LAST_MESSAGE"
    entities: list[str] = Field(min_length=1)
    min_score: float = Field(0.5, ge=0, le=1)
    min_score_overrides: dict[str, float] = Field(default_factory=dict)
    ner: NerConfig = Field(default_factory=NerConfig)
    custom_recognizers: list[CustomRecognizer] = Field(default_factory=list)
    max_chars: int = Field(32000, ge=1000, le=1_000_000)
    on_detect: OnDetect = Field(default_factory=OnDetect)
    exceptions: list[L0Exception] = Field(default_factory=list)


class L1aRule(ContractModel):
    id: str = Field(min_length=1)
    description: str | None = None
    when: ConditionGroup
    effect: Action
    severity: Severity = Severity.MEDIUM
    tags: list[str] = Field(default_factory=list)
    final: bool = False
    force_review: bool = False
    redact: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _effect_rules(self) -> L1aRule:
        if self.final and self.effect != Action.ALLOW:
            raise ValueError("final: true is only valid with effect ALLOW")
        if self.redact and self.effect != Action.ALLOW_WITH_MODIFICATIONS:
            raise ValueError("redact is only valid with effect ALLOW_WITH_MODIFICATIONS")
        return self


class HeuristicRule(ContractModel):
    """``umai-engine`` preflight rule shape, kept for compatibility."""

    id: str
    mode: Literal["REGEX", "EXACT"]
    pattern: str
    block_on_match: bool = True
    step_up_on_match: bool = False
    redact_on_match: bool = False
    replacement: str = "[REDACTED]"


class LegacyPreflight(ContractModel):
    target: TargetScope = "LAST_MESSAGE"
    rules: list[HeuristicRule] = Field(default_factory=list)
    max_length: int | None = None


class L1aConfig(ContractModel):
    rules: list[L1aRule] = Field(default_factory=list)
    legacy_preflight: LegacyPreflight | None = None


# ------------------------------------------------------------------ L1b questions


class ChoiceOption(ContractModel):
    value: str = Field(pattern=_SNAKE)
    description: str | None = None


class ConditionQuestion(ContractModel):
    type: Literal["condition"]
    instructions: str = Field(min_length=1)
    criteria: dict[str, str] | None = None

    @field_validator("criteria", mode="before")
    @classmethod
    def _bool_keys(cls, value: Any) -> Any:
        # YAML parses `true:` / `false:` keys as booleans.
        if isinstance(value, dict):
            return {str(k).lower(): v for k, v in value.items()}
        return value

    @field_validator("criteria")
    @classmethod
    def _true_false_only(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is not None and set(value) - {"true", "false"}:
            raise ValueError("criteria keys must be 'true' and/or 'false'")
        return value


class ChoiceQuestion(ContractModel):
    type: Literal["choice"]
    instructions: str = Field(min_length=1)
    options: list[ChoiceOption] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def _unique(self) -> ChoiceQuestion:
        values = [o.value for o in self.options]
        if len(values) != len(set(values)):
            raise ValueError("choice option values must be unique")
        return self


class ScoreLevel(ContractModel):
    label: str = Field(min_length=1)
    description: str | None = None


class ScoreQuestion(ContractModel):
    type: Literal["score"]
    instructions: str = Field(min_length=1)
    levels: list[ScoreLevel] = Field(min_length=2, max_length=10)


Question = Annotated[
    ConditionQuestion | ChoiceQuestion | ScoreQuestion, Field(discriminator="type")
]


class DecideRule(ContractModel):
    when: dict[str, Any] | None = None
    action: Action | None = None
    severity: Severity = Severity.MEDIUM
    tags: list[str] = Field(default_factory=list)
    redact: list[str] = Field(default_factory=list)
    default: Action | None = None

    @model_validator(mode="after")
    def _shape(self) -> DecideRule:
        if self.default is not None:
            if self.when is not None or self.action is not None:
                raise ValueError("a default rule cannot also have when/action")
        elif self.when is None or self.action is None:
            raise ValueError("a decide rule needs when + action, or default")
        return self


class ReviewWhen(ContractModel):
    actions: list[Action] = Field(default_factory=list)
    or_confidence_below: float | None = Field(None, ge=0, le=1)


class DecisionPolicy(ContractModel):
    id: str = Field(min_length=1)
    version: int = Field(ge=1)
    phases: list[Phase] = Field(min_length=1)
    policy_text: str = Field(min_length=1, max_length=4000)
    questions: dict[str, Question] = Field(min_length=1)
    decide: list[DecideRule] = Field(min_length=1)
    review_when: ReviewWhen = Field(default_factory=ReviewWhen)
    condition_threshold: float = Field(0.5, ge=0, le=1)

    @field_validator("questions")
    @classmethod
    def _qid_snake(cls, value: dict[str, Any]) -> dict[str, Any]:
        import re

        for qid in value:
            if not re.match(_SNAKE, qid):
                raise ValueError(f"question id {qid!r} must be snake_case")
        return value

    @model_validator(mode="after")
    def _ends_with_default(self) -> DecisionPolicy:
        if self.decide[-1].default is None:
            raise ValueError("decide must end with a default rule")
        if any(rule.default is not None for rule in self.decide[:-1]):
            raise ValueError("only the last decide rule may be a default")
        return self


class L1bConfig(ContractModel):
    enabled: bool = True
    provider: str | None = None
    fallback_provider: str | None = None
    shadow_providers: list[str] = Field(default_factory=list)
    timeout_ms: int = Field(600, ge=10, le=10_000)
    max_input_chars: int = Field(4000, ge=100, le=100_000)
    policies: list[DecisionPolicy] = Field(default_factory=list)


# ------------------------------------------------------------------ L2


class L2History(ContractModel):
    window_s: int = Field(1800, ge=0)
    after_s: int = Field(300, ge=0)
    max_records: int = Field(20, ge=0, le=200)


class FindingThreshold(ContractModel):
    severity: Severity = Severity.HIGH
    confidence: Literal["low", "medium", "high"] = "medium"


class L2Config(ContractModel):
    enabled: bool = False
    review_when: ConditionGroup | None = None
    model_ref: str | None = None
    reasoning_effort: Literal["low", "medium", "high"] = "medium"
    prompt: str = "l2-review.v1"
    history: L2History = Field(default_factory=L2History)
    finding_threshold: FindingThreshold = Field(default_factory=FindingThreshold)
    max_attempts: int = Field(5, ge=1, le=50)


# ------------------------------------------------------------------ bundle


class PolicyBundle(ContractModel):
    contract_version: Literal["0.3", "0.4"]
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._\-]{0,127}$")
    version: int = Field(ge=1)
    mode: Mode
    phases: list[Phase] = Field(min_length=1)
    on_evaluation_failure: FailurePolicy = FailurePolicy.FAIL_CLOSED
    l0: L0Config
    l1a: L1aConfig = Field(default_factory=L1aConfig)
    l1b: L1bConfig = Field(default_factory=L1bConfig)
    l2: L2Config = Field(default_factory=L2Config)

    def has_l1_logic(self) -> bool:
        """True when L1 has anything to evaluate (rules, legacy preflight or policies)."""

        return bool(
            self.l1a.rules
            or (self.l1a.legacy_preflight and self.l1a.legacy_preflight.rules)
            or (self.l1b.enabled and self.l1b.policies)
        )
