"""Facts: the structured view of a request that deterministic rules evaluate.

Shared by the L0 gate exceptions (ADR-0014) and L1a. Facts never contain content or entity
values — only request metadata and L0 entity types/counts.
"""

from __future__ import annotations

from typing import Any

from umai_decision_engine.contracts.entities import EntityDetectionResult
from umai_decision_engine.contracts.evaluation import EvaluationRequest

Facts = dict[str, Any]


def build_facts(request: EvaluationRequest, l0: EntityDetectionResult | None) -> Facts:
    facts: Facts = {"phase": str(request.phase)}

    subject = request.subject
    for name in ("user_id", "role", "department", "agent_id", "session_id"):
        value = getattr(subject, name)
        if value is not None:
            facts[f"subject.{name}"] = value

    if request.destination.app is not None:
        facts["destination.app"] = request.destination.app
    facts["destination.class"] = str(request.destination.class_)

    action = request.action
    if action is not None:
        for name in ("name", "tool_name", "server_name", "method", "memory_scope"):
            value = getattr(action, name)
            if value is not None:
                facts[f"action.{name}"] = value
        # Aliases used in rule documents (docs/architecture/l1-policy-decision.md).
        if action.server_name is not None:
            facts["mcp.server_name"] = action.server_name
        if action.method is not None:
            facts["mcp.method"] = action.method
        if action.memory_scope is not None:
            facts["memory.scope"] = action.memory_scope

    if l0 is not None:
        facts["entities.types"] = [s.type for s in l0.summary]
        for summary in l0.summary:
            facts[f"entities.{summary.type}.count"] = summary.count
            facts[f"entities.{summary.type}.distinct"] = summary.distinct
        facts["content.language"] = l0.language
    return facts


def lookup(facts: Facts, path: str) -> tuple[bool, Any]:
    """Return (present, value). Entity counts default to 0 for any type."""

    if path in facts:
        return True, facts[path]
    if path.startswith("entities.") and path.endswith((".count", ".distinct")):
        return True, 0
    if path == "entities.types":
        return True, []
    return False, None
