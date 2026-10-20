"""Policy bundle (docs/contracts/policy-bundle.md v0.7, decision-questions.md v0.3)."""

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


# Guardrail policies (ADR-0021): a named, versioned group of detectors with one action, the
# message roles it screens (Lakera's user / assistant / tool / tool_call, plus memory) and a
# sensitivity level (Lakera's L1–L4 → minimum detection score).
Role = Literal["user", "assistant", "tool", "tool_call", "memory"]
ALL_ROLES: tuple[Role, ...] = ("user", "assistant", "tool", "tool_call", "memory")
ROLE_PHASES: dict[str, frozenset[Phase]] = {
    "user": frozenset({Phase.PRE_LLM}),
    "assistant": frozenset({Phase.POST_LLM}),
    "tool_call": frozenset({Phase.TOOL_INPUT, Phase.MCP_REQUEST}),
    "tool": frozenset({Phase.TOOL_OUTPUT, Phase.MCP_RESPONSE}),
    "memory": frozenset({Phase.MEMORY_WRITE}),
}
Sensitivity = Literal["L1", "L2", "L3", "L4"]
# L1 lenient (fewest false positives) … L4 paranoid (fewest misses). L3 = the engine's calibrated
# threshold 0.5; context-required shapes stay context-required at every level.
SENSITIVITY_MIN_SCORE: dict[str, float] = {"L1": 0.8, "L2": 0.65, "L3": 0.5, "L4": 0.35}


class L0Policy(ContractModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._\-]{0,63}$")
    version: int = Field(1, ge=1)  # the library version pinned into this guardrail version
    name: str | None = Field(None, max_length=120)
    description: str | None = Field(None, max_length=500)
    entities: list[str] = Field(min_length=1)
    action: DetectAction = "BLOCK"
    severity: Severity = Severity.HIGH
    roles: list[Role] = Field(default_factory=lambda: list(ALL_ROLES), min_length=1)
    sensitivity: Sensitivity = "L3"
    # Off: kept in the guardrail with its configuration, but not evaluated (0.7, ADR-0022).
    enabled: bool = True

    def phases(self) -> frozenset[Phase]:
        return frozenset().union(*(ROLE_PHASES[role] for role in self.roles))

    @property
    def min_score(self) -> float:
        return SENSITIVITY_MIN_SCORE[self.sensitivity]


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
    # Detected types: listed here (bundles ≤ 0.5) or derived from `policies` (0.6).
    entities: list[str] = Field(default_factory=list)
    min_score: float = Field(0.5, ge=0, le=1)
    min_score_overrides: dict[str, float] = Field(default_factory=dict)
    ner: NerConfig = Field(default_factory=NerConfig)
    custom_recognizers: list[CustomRecognizer] = Field(default_factory=list)
    max_chars: int = Field(32000, ge=1000, le=1_000_000)
    # UNKNOWN_LINK: domains this guardrail trusts beyond the engine's popular-domain list
    # (registrable domain, e.g. "example.com"; subdomains are covered).
    allowed_domains: list[str] = Field(default_factory=list)
    on_detect: OnDetect = Field(default_factory=OnDetect)
    # Guardrail policies (0.6): when present they replace `entities` + `on_detect`.
    policies: list[L0Policy] = Field(default_factory=list)
    exceptions: list[L0Exception] = Field(default_factory=list)

    @model_validator(mode="after")
    def _entities_from_policies(self) -> L0Config:
        if self.policies:
            if self.entities or self.on_detect.actions:
                raise ValueError("with l0.policies, do not set l0.entities or l0.on_detect.actions")
            derived: list[str] = []
            for policy in self.policies:
                derived.extend(e for e in policy.entities if e not in derived)
            object.__setattr__(self, "entities", derived)
        elif not self.entities:
            raise ValueError("l0 needs entities (or policies)")
        return self


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
    # Display data and provenance (0.9); never sent to a provider.
    name: str | None = Field(None, max_length=120)
    description: str | None = Field(None, max_length=1000)
    source: str | None = Field(None, max_length=300)
    # shadow (0.9, ADR-0040): the policy is evaluated and recorded but never changes the
    # decision; a policy enforces only once evals have set its thresholds.
    enforcement: Literal["enforce", "shadow"] = "enforce"
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
    # all: one provider call with every applicable question; per_policy: one call per policy,
    # in parallel (0.9, ADR-0040; measured, see docs/architecture/l1-policy-decision.md).
    batch: Literal["all", "per_policy"] = "all"
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
    contract_version: Literal["0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9"]
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._\-]{0,127}$")
    version: int = Field(ge=1)
    # Guardrail display data (0.6); the id stays the identifier callers use.
    name: str | None = Field(None, max_length=120)
    description: str | None = Field(None, max_length=1000)
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
