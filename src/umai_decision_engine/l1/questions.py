"""Compile a bundle's L1b policies into provider-agnostic decision requests (ADR-0040).

The judged content is what L0 scanned, with every detected entity replaced by its placeholder
(ADR-0005): the latest parts first, up to ``l1b.max_input_chars``, oldest first in the request.
Facts carry no identifiers: phase, destination class, the person's role and department,
entity types and counts, and the agent tool's name.
"""

from __future__ import annotations

from umai_decision_engine.contracts.bundle import DecisionPolicy, PolicyBundle
from umai_decision_engine.contracts.entities import EntitySummary
from umai_decision_engine.contracts.evaluation import EvaluationRequest
from umai_decision_engine.contracts.questions import (
    DecisionQuestion,
    DecisionRequest,
    ExcerptMessage,
    QuestionLevel,
    QuestionOption,
)

_ELLIPSIS = " […] "


def applicable_policies(bundle: PolicyBundle, request: EvaluationRequest) -> list[DecisionPolicy]:
    if not bundle.l1b.enabled:
        return []
    return [p for p in bundle.l1b.policies if request.phase in p.phases]


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= len(_ELLIPSIS) + 2:
        return text[:limit]
    head = (limit - len(_ELLIPSIS)) // 2
    tail = limit - len(_ELLIPSIS) - head
    return text[:head] + _ELLIPSIS + text[-tail:]


def excerpt(parts: list[tuple[str, str]], max_chars: int) -> list[ExcerptMessage]:
    """The latest parts that fit in ``max_chars``; the last part is always kept (clipped in
    the middle when it alone is too long)."""

    chosen: list[ExcerptMessage] = []
    budget = max_chars
    for role, text in reversed(parts):
        if not chosen:
            clipped = _clip(text, budget)
            chosen.append(ExcerptMessage(role=_role(role), content=clipped))
            budget -= len(clipped)
            continue
        if len(text) > budget:
            break
        chosen.append(ExcerptMessage(role=_role(role), content=text))
        budget -= len(text)
    return list(reversed(chosen))


def _role(role: str) -> str:
    return role if role in ("system", "user", "assistant", "tool") else "user"


def facts(request: EvaluationRequest, summary: list[EntitySummary]) -> dict[str, object]:
    subject = request.subject
    action = request.action
    return {
        "phase": str(request.phase),
        "destination": {"class": str(request.destination.class_)},
        "subject": {
            k: v for k, v in (("role", subject.role), ("department", subject.department)) if v
        },
        "entities": [{"type": e.type, "count": e.count, "distinct": e.distinct} for e in summary],
        **(
            {"tool": action.tool_name or action.name}
            if action and (action.tool_name or action.name)
            else {}
        ),
    }


def questions_of(policy: DecisionPolicy) -> list[DecisionQuestion]:
    compiled: list[DecisionQuestion] = []
    for qid, question in policy.questions.items():
        namespaced = f"{policy.id}.{qid}"
        if question.type == "choice":
            compiled.append(
                DecisionQuestion(
                    qid=namespaced,
                    type="choice",
                    instructions=question.instructions,
                    options=[
                        QuestionOption(value=o.value, description=o.description)
                        for o in question.options
                    ],
                )
            )
        elif question.type == "score":
            compiled.append(
                DecisionQuestion(
                    qid=namespaced,
                    type="score",
                    instructions=question.instructions,
                    levels=[
                        QuestionLevel(label=lv.label, description=lv.description)
                        for lv in question.levels
                    ],
                )
            )
        else:
            compiled.append(
                DecisionQuestion(
                    qid=namespaced,
                    type="condition",
                    instructions=question.instructions,
                    criteria=question.criteria,
                )
            )
    return compiled


def decision_request(
    request_id: str,
    policies: list[DecisionPolicy],
    judged: list[ExcerptMessage],
    known: dict[str, object],
) -> DecisionRequest:
    return DecisionRequest(
        request_id=request_id,
        policies=[
            {"id": p.id, "version": p.version, "policy_text": p.policy_text} for p in policies
        ],
        facts=known,
        excerpt=judged,
        questions=[q for p in policies for q in questions_of(p)],
    )
