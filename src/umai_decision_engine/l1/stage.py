"""The L1b stage: applicable policies → decision-model call(s) → mapped outcomes (ADR-0040).

- Enforcing policies are decided synchronously within the runtime budget; a provider failure
  tries ``l1b.fallback_provider`` when the budget allows, else the bundle's failure policy
  applies to the L1b part (docs/architecture/decision-merge-and-failure.md).
- Shadow policies never change the decision. With enforcing policies they ride on the same
  call; without any, they run in the background after the decision is returned, so they add
  no latency. Their outcomes are logged (ids, actions, probabilities; never content).
- ``l1b.batch``: ``all`` sends every applicable question in one call; ``per_policy`` sends
  one call per policy, in parallel.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from umai_decision_engine.contracts.bundle import DecisionPolicy, PolicyBundle
from umai_decision_engine.contracts.common import (
    ACTION_RANK,
    ALLOWED_ACTIONS,
    Action,
    Layer,
    Severity,
)
from umai_decision_engine.contracts.entities import EntitySummary
from umai_decision_engine.contracts.evaluation import (
    Decision,
    ErrorInfo,
    EvaluationRequest,
    MatchedRule,
    Tag,
    TraceEntry,
)
from umai_decision_engine.contracts.questions import Answer, DecisionRequest, ExcerptMessage
from umai_decision_engine.l1.decision_models.base import DecisionModelError
from umai_decision_engine.l1.decision_models.registry import DecisionModels
from umai_decision_engine.l1.mapping import PolicyOutcome, decide
from umai_decision_engine.l1.questions import (
    applicable_policies,
    decision_request,
    excerpt,
    facts,
)

logger = logging.getLogger("umai.de.l1b")

_SEVERITY_RANK = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}
_RETRYABLE = frozenset({"timeout", "rate_limited", "unavailable"})


@dataclass
class L1bResult:
    ran: bool = False  # at least one enforcing policy applied
    decision: Decision | None = None  # None with ``failed``: the caller applies its failure policy
    failed: str | None = None  # error kind when the enforcing call failed
    tags: list[Tag] = field(default_factory=list)
    errors: list[ErrorInfo] = field(default_factory=list)
    trace: list[TraceEntry] = field(default_factory=list)
    degraded: bool = False
    review: bool = False
    latency_ms: float | None = None


def stricter(a: Decision, b: Decision) -> bool:
    """Whether ``a`` is stricter than ``b`` (rank, then severity)."""

    return (ACTION_RANK[a.action], _SEVERITY_RANK[a.severity]) > (
        ACTION_RANK[b.action],
        _SEVERITY_RANK[b.severity],
    )


def _outcome_label(outcome: PolicyOutcome) -> str:
    p = "-" if outcome.probability is None else f"{outcome.probability:.3f}"
    return f"{outcome.policy.id}@{outcome.policy.version}:{outcome.action}:{p}"


class L1bStage:
    def __init__(self, models: DecisionModels) -> None:
        self._models = models
        self._background: set[asyncio.Task] = set()

    async def aclose(self) -> None:
        await self._models.aclose()

    async def drain(self) -> None:
        """Wait for background shadow evaluations (tests, shutdown)."""

        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    # ------------------------------------------------------------------ entry point

    async def run(
        self,
        bundle: PolicyBundle,
        request: EvaluationRequest,
        parts: list[tuple[str, str]],
        summary: list[EntitySummary],
        *,
        remaining_s: float,
        event_id: str,
    ) -> L1bResult:
        policies = applicable_policies(bundle, request)
        enforce = [p for p in policies if p.enforcement == "enforce"]
        shadow = [p for p in policies if p.enforcement == "shadow"]
        result = L1bResult(ran=bool(enforce))
        if not policies:
            return result
        if not request.flags.allow_model_calls:
            if enforce:
                result.degraded = True
                result.trace.append(
                    TraceEntry(layer=Layer.L1B, outcome="model_calls_disabled", latency_ms=0.0)
                )
            return result
        judged = excerpt(parts, bundle.l1b.max_input_chars)
        known = facts(request, summary)
        if not enforce:
            self._spawn_shadow(bundle, request, shadow, judged, known, event_id)
            result.trace.append(
                TraceEntry(layer=Layer.L1B, outcome="shadow_background", latency_ms=0.0)
            )
            return result

        started = time.perf_counter()
        timeout_s = bundle.l1b.timeout_ms / 1000
        if timeout_s > remaining_s:
            return self._failed(
                result, bundle, request, "timeout", "no runtime budget left for L1b", started
            )
        try:
            answers = await self._answers(
                bundle, request.request_id, enforce + shadow, judged, known, timeout_s, started
            )
        except DecisionModelError as exc:
            return self._failed(result, bundle, request, exc.kind, str(exc), started)

        outcomes = [decide(p, answers) for p in enforce + shadow]
        self._apply(result, [o for o in outcomes if not o.shadow])
        result.latency_ms = round((time.perf_counter() - started) * 1000, 3)
        result.trace.append(
            TraceEntry(
                layer=Layer.L1B,
                outcome=str(result.decision.action) if result.decision else "none",
                latency_ms=result.latency_ms,
            )
        )
        self._log(request, event_id, bundle, outcomes, result.latency_ms, review=result.review)
        return result

    # ------------------------------------------------------------------ calls

    async def _call(
        self, bundle: PolicyBundle, decision: DecisionRequest, timeout_s: float, started: float
    ) -> dict[str, Answer]:
        primary = self._models.get(bundle.l1b.provider)
        if primary is None:
            raise DecisionModelError("unavailable", "the L1b provider is not configured")
        try:
            return (await primary.decide(decision, timeout_s=timeout_s)).answers
        except DecisionModelError:
            fallback = self._models.get(bundle.l1b.fallback_provider)
            left = timeout_s - (time.perf_counter() - started)
            if fallback is None or left < timeout_s / 2:
                raise
            return (await fallback.decide(decision, timeout_s=left)).answers

    async def _answers(
        self,
        bundle: PolicyBundle,
        request_id: str,
        policies: list[DecisionPolicy],
        judged: list[ExcerptMessage],
        known: dict[str, object],
        timeout_s: float,
        started: float,
    ) -> dict[str, Answer]:
        if bundle.l1b.batch == "all":
            request = decision_request(request_id, policies, judged, known)
            return await self._call(bundle, request, timeout_s, started)
        calls = [
            self._call(bundle, decision_request(request_id, [p], judged, known), timeout_s, started)
            for p in policies
        ]
        answers: dict[str, Answer] = {}
        for part in await asyncio.gather(*calls):
            answers.update(part)
        return answers

    # ------------------------------------------------------------------ outcomes

    def _apply(self, result: L1bResult, outcomes: list[PolicyOutcome]) -> None:
        winner: PolicyOutcome | None = None
        for outcome in outcomes:
            if outcome.review_reasons:
                result.review = True
            if outcome.rule is None:
                continue
            result.tags.extend(
                Tag(
                    code=tag,
                    layer=Layer.L1B,
                    probability=outcome.probability,
                    policy_id=outcome.policy.id,
                    policy_version=outcome.policy.version,
                )
                for tag in outcome.tags
            )
            if winner is None or (ACTION_RANK[outcome.action], _SEVERITY_RANK[outcome.severity]) > (
                ACTION_RANK[winner.action],
                _SEVERITY_RANK[winner.severity],
            ):
                winner = outcome
        matched = [
            MatchedRule(
                kind="l1b_policy",
                id=o.policy.id,
                version=o.policy.version,
                name=o.policy.name,
                action=o.action,
            )
            for o in sorted(
                (o for o in outcomes if o.rule is not None),
                key=lambda o: (-ACTION_RANK[o.action], -_SEVERITY_RANK[o.severity]),
            )
        ]
        if winner is None:
            defaults = sorted((o.action for o in outcomes), key=lambda a: ACTION_RANK[a])
            action = defaults[-1] if defaults else Action.ALLOW
            result.decision = Decision(
                action=action,
                allowed=action in ALLOWED_ACTIONS,
                severity=Severity.LOW,
                reason="L1b: no policy rule matched",
                decided_by="L1b",
            )
            return
        result.decision = Decision(
            action=winner.action,
            allowed=winner.action in ALLOWED_ACTIONS,
            severity=winner.severity,
            reason=f"L1b policy {winner.policy.id}"
            + (f" ({', '.join(winner.tags)})" if winner.tags else ""),
            decided_by="L1b",
            matched=matched,
        )

    def _failed(
        self,
        result: L1bResult,
        bundle: PolicyBundle,
        request: EvaluationRequest,
        kind: str,
        message: str,
        started: float,
    ) -> L1bResult:
        result.degraded = True
        result.latency_ms = round((time.perf_counter() - started) * 1000, 3)
        result.errors.append(
            ErrorInfo(
                layer=Layer.L1B,
                type=f"L1B_{kind.upper()}",
                message=message[:200],
                retryable=kind in _RETRYABLE,
            )
        )
        result.trace.append(
            TraceEntry(layer=Layer.L1B, outcome=f"error:{kind}", latency_ms=result.latency_ms)
        )
        result.failed = kind
        logger.warning(
            "l1b.failed request_id=%s bundle=%s@%s kind=%s",
            request.request_id,
            bundle.id,
            bundle.version,
            kind,
        )
        return result

    # ------------------------------------------------------------------ shadow

    def _spawn_shadow(
        self,
        bundle: PolicyBundle,
        request: EvaluationRequest,
        policies: list[DecisionPolicy],
        judged: list[ExcerptMessage],
        known: dict[str, object],
        event_id: str,
    ) -> None:
        async def go() -> None:
            started = time.perf_counter()
            try:
                answers = await self._answers(
                    bundle,
                    request.request_id,
                    policies,
                    judged,
                    known,
                    bundle.l1b.timeout_ms / 1000,
                    started,
                )
            except DecisionModelError as exc:
                logger.warning(
                    "l1b.shadow_failed request_id=%s event_id=%s bundle=%s@%s kind=%s",
                    request.request_id,
                    event_id,
                    bundle.id,
                    bundle.version,
                    exc.kind,
                )
                return
            outcomes = [decide(p, answers) for p in policies]
            latency = round((time.perf_counter() - started) * 1000, 3)
            self._log(request, event_id, bundle, outcomes, latency, review=False)

        task = asyncio.create_task(go())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _log(
        self,
        request: EvaluationRequest,
        event_id: str,
        bundle: PolicyBundle,
        outcomes: list[PolicyOutcome],
        latency_ms: float,
        *,
        review: bool,
    ) -> None:
        enforced = [_outcome_label(o) for o in outcomes if not o.shadow]
        shadow = [_outcome_label(o) for o in outcomes if o.shadow]
        logger.info(
            "l1b request_id=%s event_id=%s bundle=%s@%s phase=%s provider=%s latency_ms=%s "
            "enforce=%s shadow=%s review=%s",
            request.request_id,
            event_id,
            bundle.id,
            bundle.version,
            request.phase,
            bundle.l1b.provider,
            latency_ms,
            ",".join(enforced) or "-",
            ",".join(shadow) or "-",
            review,
        )
