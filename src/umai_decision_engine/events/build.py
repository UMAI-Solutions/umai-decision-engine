"""Records built from an evaluation (ADR-0041): ids, types, counts, codes, actions,
probabilities and latencies. Nothing here sees content: the inputs are the request's routing
fields and the response, and the response itself carries no content except
``output_modifications``, which is never read here."""

from __future__ import annotations

from datetime import datetime

from umai_decision_engine import __version__
from umai_decision_engine.contracts.bundle import DecisionPolicy
from umai_decision_engine.contracts.evaluation import (
    Decision,
    EvaluationRequest,
    EvaluationResponse,
)
from umai_decision_engine.contracts.events import (
    DecisionEvent,
    EventAgentAction,
    EventBundle,
    EventDecision,
    EventError,
    EventL1b,
    EventLatency,
    EventMatched,
    EventSubject,
    L1bOutcomeRecord,
    RecordContext,
    UsageRecord,
)

API = RecordContext()


def _unit(value: float | None) -> float | None:
    """A probability summed over several values can drift past 1 by rounding."""

    return None if value is None else min(max(value, 0.0), 1.0)


def decision_event(
    request: EvaluationRequest,
    response: EvaluationResponse,
    context: RecordContext | None,
    occurred_at: datetime,
    *,
    floor: Decision | None = None,
    review: bool = False,
    review_reasons: list[str] | None = None,
    l1b: EventL1b | None = None,
) -> DecisionEvent:
    context = context or API
    decision = response.decision
    subject = request.subject
    agent = request.action
    return DecisionEvent(
        event_id=response.event_id,
        request_id=response.request_id,
        tenant_id=response.tenant_id,
        occurred_at=occurred_at,
        source=context.source,
        engine_version=__version__,
        key_id=context.key_id,
        environment=context.environment,
        application_id=context.application_id,
        gateway_call_id=context.gateway_call_id,
        bundle=EventBundle(
            id=response.bundle.id,
            version=response.bundle.version,
            sha256=response.bundle.sha256,
            mode=response.bundle.mode,
        ),
        phase=response.phase,
        subject=EventSubject(role=subject.role, department=subject.department)
        if subject.role or subject.department
        else None,
        destination=request.destination,
        agent_action=EventAgentAction(tool_name=agent.tool_name, server_name=agent.server_name)
        if agent is not None and (agent.tool_name or agent.server_name)
        else None,
        entities=response.entities,
        tags=response.tags,
        matched=[
            EventMatched(kind=m.kind, id=m.id, version=m.version, action=m.action)
            for m in decision.matched
        ],
        decision=EventDecision(
            action=decision.action,
            severity=decision.severity,
            decided_by=decision.decided_by,
            degraded=response.degraded,
            floor=floor.action if floor is not None else None,
            would_have=response.would_have.action if response.would_have else None,
        ),
        l1b=l1b,
        review=review,
        review_reasons=sorted(set(review_reasons or [])),
        errors=[
            EventError(layer=e.layer, type=e.type, retryable=e.retryable) for e in response.errors
        ],
        latency_ms=EventLatency(
            total=response.latency_ms.total,
            l0=response.latency_ms.l0,
            l1b=response.latency_ms.l1b,
        ),
    )


def evaluation_usage(
    response: EvaluationResponse, context: RecordContext | None, occurred_at: datetime
) -> UsageRecord:
    """The ``evaluations`` meter: one per admitted evaluation (usage 0.1)."""

    context = context or API
    return UsageRecord(
        event_id=response.event_id,
        tenant_id=response.tenant_id,
        occurred_at=occurred_at,
        meter="evaluations",
        quantity=1,
        source=context.source,
        key_id=context.key_id,
        environment=context.environment,
        application_id=context.application_id,
        bundle_id=response.bundle.id,
        bundle_version=response.bundle.version,
        decision=response.decision.action,
        degraded=response.degraded,
    )


def l1b_usage(
    *,
    event_id: str,
    tenant_id: str,
    bundle_id: str,
    bundle_version: int | None,
    context: RecordContext | None,
    occurred_at: datetime,
    calls: int,
    input_tokens: int,
    output_tokens: int,
) -> list[UsageRecord]:
    """``l1b_calls`` and, when the provider reported them, the token meters (usage 0.1)."""

    context = context or API
    quantities = {
        "l1b_calls": calls,
        "l1b_input_tokens": input_tokens,
        "l1b_output_tokens": output_tokens,
    }
    return [
        UsageRecord(
            event_id=event_id,
            tenant_id=tenant_id,
            occurred_at=occurred_at,
            meter=meter,
            quantity=quantity,
            source=context.source,
            key_id=context.key_id,
            environment=context.environment,
            application_id=context.application_id,
            bundle_id=bundle_id,
            bundle_version=bundle_version,
        )
        for meter, quantity in quantities.items()
        if meter == "l1b_calls" or quantity > 0
    ]


def gateway_usage(
    context: RecordContext,
    *,
    tenant_id: str,
    occurred_at: datetime,
    bundle_id: str | None,
    provider: str | None,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> list[UsageRecord]:
    """One gateway call (usage 0.2): ``gateway_calls`` and the provider's token counts when it
    reported them. ``event_id`` is the call's id."""

    assert context.gateway_call_id is not None
    quantities = {
        "gateway_calls": 1,
        "provider_input_tokens": input_tokens,
        "provider_output_tokens": output_tokens,
    }
    return [
        UsageRecord(
            event_id=context.gateway_call_id,
            tenant_id=tenant_id,
            occurred_at=occurred_at,
            meter=meter,
            quantity=quantity,
            source=context.source,
            key_id=context.key_id,
            environment=context.environment,
            application_id=context.application_id,
            bundle_id=bundle_id,
            provider=provider,
            model=model,
        )
        for meter, quantity in quantities.items()
        if quantity is not None and (meter == "gateway_calls" or quantity > 0)
    ]


def l1b_outcome(
    *,
    event_id: str,
    tenant_id: str,
    bundle_id: str,
    bundle_version: int | None,
    request: EvaluationRequest,
    policy: DecisionPolicy,
    provider: str | None,
    occurred_at: datetime,
    latency_ms: float | None,
    outcome=None,  # l1.mapping.PolicyOutcome; duck-typed so events does not import l1
    error: str | None = None,
) -> L1bOutcomeRecord:
    decided = outcome is not None
    return L1bOutcomeRecord(
        event_id=event_id,
        tenant_id=tenant_id,
        occurred_at=occurred_at,
        bundle_id=bundle_id,
        bundle_version=bundle_version,
        phase=request.phase,
        policy_id=policy.id,
        policy_version=policy.version,
        enforcement=policy.enforcement,
        provider=provider,
        outcome="decided" if decided else "failed",
        action=outcome.action if decided else None,
        severity=outcome.severity if decided else None,
        probability=_unit(outcome.probability) if decided else None,
        rule=outcome.rule if decided else None,
        tags=list(outcome.tags) if decided else [],
        review_reasons=list(outcome.review_reasons) if decided else [],
        error=None if decided else error,
        latency_ms=latency_ms,
    )
