"""Run one guardrail check of a call through the in-process pipeline (ADR-0034 §2, §7).

The gateway builds an ``EvaluationRequest`` from its segments, scans all of them (target
``FULL_HISTORY``, a widening of the bundle's ``l0.target``), and maps L0's spans back to the
segments so masked types can be replaced in place.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import UTC, datetime

from umai_decision_engine.contracts.common import ACTION_RANK, Action, Phase
from umai_decision_engine.contracts.evaluation import (
    AgentAction,
    BundleRef,
    ChatMessage,
    Content,
    Decision,
    Destination,
    EvaluationRequest,
    EvaluationResponse,
    Subject,
)
from umai_decision_engine.contracts.events import RecordContext
from umai_decision_engine.contracts.gateway import Route
from umai_decision_engine.gateway.content import segment_offsets
from umai_decision_engine.gateway.masking import Span
from umai_decision_engine.pipeline.orchestrator import Evaluated, Evaluator
from umai_decision_engine.policy.store import BundleStore


@dataclass(frozen=True)
class CallContext:
    request_id: str
    tenant_id: str
    route: Route
    subject: Subject
    # Who called, for the records of every check and of the call (ADR-0041 §4)
    record: RecordContext = field(default_factory=lambda: RecordContext(source="gateway"))


@dataclass(frozen=True)
class Check:
    phase: Phase
    evaluated: Evaluated
    spans: list[list[Span]] = field(default_factory=list)  # per input text, masked types only

    @property
    def response(self) -> EvaluationResponse:
        return self.evaluated.response

    @property
    def decision(self) -> Decision:
        return self.evaluated.response.decision

    @property
    def allowed(self) -> bool:
        return self.decision.allowed

    @property
    def masks(self) -> bool:
        return any(self.spans)


class Guard:
    def __init__(self, evaluator: Evaluator, store: BundleStore) -> None:
        self._evaluator = evaluator
        self._store = store

    def phases(self, ctx: CallContext) -> frozenset[Phase]:
        """Phases the route's guardrail enables (empty when it is not loaded: the check then
        runs anyway and the pipeline answers CONFIG_MISSING with its fixed BLOCK)."""

        loaded = self._store.get(ctx.route.guardrail, None, tenant_id=ctx.tenant_id)
        return frozenset(loaded.bundle.phases) if loaded else frozenset()

    def enabled(self, ctx: CallContext, phase: Phase) -> bool:
        phases = self.phases(ctx)
        return not phases or phase in phases

    async def check(
        self,
        ctx: CallContext,
        phase: Phase,
        texts: list[tuple[str, str]],  # (evaluation role, text)
        *,
        action: AgentAction | None = None,
        record: bool = True,
    ) -> Check:
        """``record=False`` holds the check's records on the result: :meth:`commit` writes them
        (a stream's window re-check that decided), otherwise they are dropped."""

        route = ctx.route
        request = EvaluationRequest(
            contract_version="0.9",
            request_id=ctx.request_id,
            timestamp=datetime.now(UTC),
            tenant_id=ctx.tenant_id,
            bundle=BundleRef(id=route.guardrail),
            phase=phase,
            content=Content(
                messages=[ChatMessage(role=role, content=text) for role, text in texts],  # type: ignore[arg-type]
                focus="LAST_ASSISTANT_MESSAGE" if phase == Phase.POST_LLM else "LAST_USER_MESSAGE",
            ),
            subject=ctx.subject,
            destination=Destination(
                app=f"{route.upstream.kind}/{route.upstream.model}",
                class_=route.destination_class,
            ),
            action=action,
        )
        context = ctx.record if record else ctx.record.model_copy(update={"record": False})
        evaluated = await self._evaluator.evaluate_detailed(
            request, target="FULL_HISTORY", context=context
        )
        return Check(phase, evaluated, _spans_by_text(evaluated, [t for _, t in texts]))

    def commit(self, check: Check) -> None:
        self._evaluator.commit(check.evaluated)

    def record_call(self, context: RecordContext, **call: object) -> None:
        """The call's usage records (usage 0.2), built by the pipeline."""

        self._evaluator.record_call(context, **call)  # type: ignore[arg-type]


def _spans_by_text(evaluated: Evaluated, texts: list[str]) -> list[list[Span]]:
    spans: list[list[Span]] = [[] for _ in texts]
    if not evaluated.masked_types or not texts:
        return spans
    starts = segment_offsets(texts)
    for entity in evaluated.entities:
        if entity.type not in evaluated.masked_types:
            continue
        i = bisect.bisect_right(starts, entity.start) - 1
        if i < 0:
            continue
        local_start = entity.start - starts[i]
        local_end = min(entity.end - starts[i], len(texts[i]))
        if local_start < local_end:
            spans[i].append((local_start, local_end, entity.type, entity.value_hash))
    return spans


def strictest(checks: list[Check]) -> Check | None:
    return max(checks, key=lambda c: ACTION_RANK[c.decision.action], default=None)


BLOCKING = frozenset({Action.BLOCK, Action.STEP_UP_APPROVAL})
