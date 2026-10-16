"""Shared enums and base model (docs/contracts/README.md)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Base for every contract model: unknown fields are errors, not silently ignored."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Phase(StrEnum):
    PRE_LLM = "PRE_LLM"
    POST_LLM = "POST_LLM"
    TOOL_INPUT = "TOOL_INPUT"
    TOOL_OUTPUT = "TOOL_OUTPUT"
    MCP_REQUEST = "MCP_REQUEST"
    MCP_RESPONSE = "MCP_RESPONSE"
    MEMORY_WRITE = "MEMORY_WRITE"


# Phases without a user interface: step_up behaves as fail_closed there.
NON_INTERACTIVE_PHASES = frozenset(
    {Phase.TOOL_INPUT, Phase.TOOL_OUTPUT, Phase.MCP_REQUEST, Phase.MCP_RESPONSE, Phase.MEMORY_WRITE}
)


class Action(StrEnum):
    ALLOW = "ALLOW"
    ALLOW_WITH_WARNINGS = "ALLOW_WITH_WARNINGS"
    ALLOW_WITH_MODIFICATIONS = "ALLOW_WITH_MODIFICATIONS"
    STEP_UP_APPROVAL = "STEP_UP_APPROVAL"
    BLOCK = "BLOCK"


ACTION_RANK: dict[Action, int] = {
    Action.ALLOW: 1,
    Action.ALLOW_WITH_WARNINGS: 2,
    Action.ALLOW_WITH_MODIFICATIONS: 3,
    Action.STEP_UP_APPROVAL: 4,
    Action.BLOCK: 5,
}

ALLOWED_ACTIONS = frozenset(
    {Action.ALLOW, Action.ALLOW_WITH_WARNINGS, Action.ALLOW_WITH_MODIFICATIONS}
)


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


SEVERITY_RANK: dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


class Mode(StrEnum):
    ENFORCE = "ENFORCE"
    MONITOR = "MONITOR"


class Layer(StrEnum):
    L0 = "L0"
    L1A = "L1a"
    L1B = "L1b"
    L2 = "L2"
    PIPELINE = "pipeline"


class DestinationClass(StrEnum):
    EXTERNAL_LLM = "external_llm"
    INTERNAL_LLM = "internal_llm"
    INTERNAL_APP = "internal_app"
    EXTERNAL_APP = "external_app"
    AGENT_TOOL = "agent_tool"
    UNKNOWN = "unknown"


class FailurePolicy(StrEnum):
    FAIL_CLOSED = "fail_closed"
    FAIL_OPEN = "fail_open"
    STEP_UP = "step_up"
