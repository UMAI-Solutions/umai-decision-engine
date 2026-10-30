"""Runtime evaluation: resolve bundle → L0 → L0 gate → L1 → final decision
(docs/architecture/decision-merge-and-failure.md).

- L0 gate (ADR-0014): a detection's ``BLOCK`` / ``STEP_UP_APPROVAL`` is final; any other gate
  outcome (masking, warning, an exception's allow) is a floor and L1 still runs, on the
  redacted text (ADR-0036): ``final = max(floor, L1)``.
- L1b (ADR-0040): the bundle's decision policies, answered by a decision model. A provider
  failure applies the bundle's failure policy to the L1b part. Shadow policies are recorded,
  never decide, and run in their own call after the decision.
- L1a rules are not implemented yet: a bundle with L1a rules is answered with its
  ``on_evaluation_failure`` and an explicit ``L1_NOT_IMPLEMENTED`` error — never a silent allow
  (AGENTS.md rule 4).
- Records (ADR-0041): every exit hands a decision event and an ``evaluations`` usage record to
  the recorder, every L1b call its outcome records, and an evaluation's last L1b call its usage
  records. The recorder never blocks and a failing record never changes the decision (ADR-0042).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from umai_decision_engine.contracts.bundle import PolicyBundle, TargetScope
from umai_decision_engine.contracts.common import (
    ALLOWED_ACTIONS,
    Action,
    Layer,
    Mode,
    Phase,
    Severity,
)
from umai_decision_engine.contracts.entities import DetectedEntity, EntityDetectionResult
from umai_decision_engine.contracts.evaluation import (
    BundleInfo,
    Decision,
    ErrorInfo,
    EvaluationRequest,
    EvaluationResponse,
    LatencyMs,
    OutputModifications,
    Tag,
    TraceEntry,
)
from umai_decision_engine.contracts.events import EventL1b, Record, RecordContext
from umai_decision_engine.events.build import (
    decision_event,
    evaluation_usage,
    gateway_usage,
    l1b_outcome,
    l1b_usage,
)
from umai_decision_engine.events.recorder import NullRecorder, Recorder
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.l0.hashing import TenantKeyError
from umai_decision_engine.l0.recognizers.catalog import TAG_BY_ENTITY
from umai_decision_engine.l0.target import SEPARATOR, extract_target_parts
from umai_decision_engine.l1.decision_models.registry import DecisionModels
from umai_decision_engine.l1.stage import L1bReport, L1bResult, L1bStage, stricter
from umai_decision_engine.pipeline.failure import config_failure_decision, failure_decision
from umai_decision_engine.pipeline.l0_gate import decide_l0
from umai_decision_engine.pipeline.l0_policies import phase_config, type_rules
from umai_decision_engine.pipeline.l1_input import redacted_parts
from umai_decision_engine.policy.store import BundleStore, LoadedBundle
from umai_decision_engine.policy.validate import BundleValidationError
from umai_decision_engine.rules.facts import build_facts
from umai_decision_engine.settings import Settings

logger = logging.getLogger("umai.de.pipeline")


def new_event_id() -> str:
    return f"ta-{uuid.uuid4().hex}"


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 3)


def l0_tags(result: EntityDetectionResult) -> list[Tag]:
    best: dict[str, float] = {}
    for entity in result.entities:
        best[entity.type] = max(best.get(entity.type, 0.0), entity.score)
    return [
        Tag(code=TAG_BY_ENTITY.get(t, f"data.pii.custom.{t.lower()}"), layer=Layer.L0, score=s)
        for t, s in best.items()
    ]


def l1a_has_rules(bundle: PolicyBundle) -> bool:
    if bundle.l1a.rules:
        return True
    return bool(bundle.l1a.legacy_preflight and bundle.l1a.legacy_preflight.rules)


def l1b_applies(bundle: PolicyBundle, request: EvaluationRequest) -> bool:
    return bundle.l1b.enabled and any(request.phase in p.phases for p in bundle.l1b.policies)


def l1_has_work(bundle: PolicyBundle, request: EvaluationRequest) -> bool:
    return l1a_has_rules(bundle) or l1b_applies(bundle, request)


_TERMINAL_L0 = frozenset({Action.BLOCK, Action.STEP_UP_APPROVAL})


def with_floor(floor: Decision | None, decision: Decision) -> Decision:
    """``max(floor, decision)``; the floor wins ties. Matched rules of both are kept, the
    winner's first."""

    if floor is None:
        return decision
    if stricter(decision, floor):
        return decision.model_copy(update={"matched": decision.matched + floor.matched})
    return floor.model_copy(update={"matched": floor.matched + decision.matched})


@dataclass(frozen=True)
class Evaluated:
    """An evaluation plus what in-process raw-content callers (the gateway, ADR-0035) need to
    apply it: the scanned text, L0's entity spans over it, and the types the decision masks.
    Never serialized: spans index raw text."""

    response: EvaluationResponse
    scanned_text: str = ""
    entities: list[DetectedEntity] = field(default_factory=list)
    masked_types: frozenset[str] = frozenset()
    # Records held for the caller (``context.record`` false): :meth:`Evaluator.commit` writes
    # them; otherwise they are dropped (a gateway stream's window re-checks, ADR-0041 §4).
    records: list[Record] = field(default_factory=list)


@dataclass
class _Hold:
    """The records of an evaluation run with ``context.record`` false. Kept past the evaluation
    while its background shadow call runs: that call's records join the held ones until
    :meth:`Evaluator.commit`, and are written directly after it."""

    records: list[Record] = field(default_factory=list)
    returned: bool = False
    committed: bool = False
    background: asyncio.Task | None = None


class Evaluator:
    def __init__(
        self,
        store: BundleStore,
        detector: EntityDetector,
        settings: Settings,
        decision_models: DecisionModels | None = None,
        recorder: Recorder | None = None,
    ) -> None:
        self._store = store
        self._detector = detector
        self._settings = settings
        self.recorder: Recorder = recorder or NullRecorder()
        self._held: dict[str, _Hold] = {}  # event id → a held evaluation's records
        self.l1b = L1bStage(decision_models or DecisionModels({}), report=self._record_l1b)

    def record_call(
        self,
        context: RecordContext,
        *,
        tenant_id: str,
        occurred_at: datetime,
        bundle_id: str | None,
        provider: str | None,
        model: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        """A gateway call's usage records (usage 0.2); the gateway does not import ``events``."""

        try:
            for record in gateway_usage(
                context,
                tenant_id=tenant_id,
                occurred_at=occurred_at,
                bundle_id=bundle_id,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ):
                self.recorder.record(record)
        except Exception:  # a record never changes the answer (ADR-0042)
            logger.exception("records.build_failed gateway_call_id=%s", context.gateway_call_id)

    def commit(self, evaluated: Evaluated) -> None:
        """Write the records held on an evaluation that ran with ``context.record`` false, and
        those its shadow call still reports."""

        hold = self._held.get(evaluated.response.event_id)
        if hold is not None:
            hold.committed = True
        for record in evaluated.records:
            self.recorder.record(record)
        evaluated.records.clear()

    def resolve_bundle(
        self, request: EvaluationRequest
    ) -> tuple[LoadedBundle | None, ErrorInfo | None]:
        if request.bundle_inline is not None:
            if not self._settings.allow_inline_bundles:
                raise PermissionError("inline bundles are disabled")
            return self._store.load_inline(
                request.bundle_inline
            ), None  # may raise validation error
        # The request's tenant comes from the credential (ADR-0023 §5); only its own
        # bundles resolve (ADR-0019 §2).
        loaded = self._store.get(
            request.bundle.id, request.bundle.version, tenant_id=request.tenant_id
        )
        if loaded is None:
            what = f"v{request.bundle.version}" if request.bundle.version else "active version"
            return None, ErrorInfo(
                layer=Layer.PIPELINE,
                type="CONFIG_MISSING",
                message=f"bundle {request.bundle.id} {what} is not loaded",
                retryable=False,
            )
        return loaded, None

    async def evaluate(
        self, request: EvaluationRequest, *, context: RecordContext | None = None
    ) -> EvaluationResponse:
        return (await self.evaluate_detailed(request, context=context)).response

    async def evaluate_detailed(
        self,
        request: EvaluationRequest,
        *,
        target: TargetScope | None = None,
        context: RecordContext | None = None,
    ) -> Evaluated:
        """``target`` overrides the bundle's ``l0.target`` (the gateway scans every message it
        forwards, gateway-api 0.2); callers of the HTTP API never set it. ``context`` names the
        caller in the records (source, key, application); ``None`` records an API call without
        a key."""

        started = time.perf_counter()
        event_id = new_event_id()
        hold = _Hold() if context is not None and not context.record else None
        if hold is not None:
            self._held[event_id] = hold
        try:
            evaluated = await self._evaluate(request, target, context, started, event_id)
        finally:
            if hold is not None:
                hold.returned = True
                self._release(event_id)
        return replace(evaluated, records=hold.records) if hold is not None else evaluated

    def _release(self, event_id: str) -> None:
        """Forget a hold once its evaluation returned and its shadow call, if any, reported."""

        hold = self._held.get(event_id)
        if (
            hold is not None
            and hold.returned
            and (hold.background is None or hold.background.done())
        ):
            del self._held[event_id]

    async def _evaluate(
        self,
        request: EvaluationRequest,
        target: TargetScope | None,
        context: RecordContext | None,
        started: float,
        event_id: str,
    ) -> Evaluated:
        budget_s = (
            min(
                request.timeout_ms or self._settings.default_timeout_ms,
                self._settings.max_timeout_ms,
            )
            / 1000
        )

        try:
            loaded, missing = self.resolve_bundle(request)
        except BundleValidationError as exc:
            loaded, missing = (
                None,
                ErrorInfo(
                    layer=Layer.PIPELINE,
                    type="CONFIG_INVALID",
                    message=str(exc)[:500],
                    retryable=False,
                ),
            )
        if loaded is None:
            assert missing is not None
            response = self._config_response(
                request,
                event_id,
                started,
                missing,
                BundleInfo(
                    id=request.bundle.id if request.bundle else "inline",
                    version=request.bundle.version if request.bundle else None,
                ),
            )
            self._record(request, response, context)
            return Evaluated(response)

        bundle = loaded.bundle
        info = BundleInfo(
            id=bundle.id, version=bundle.version, mode=bundle.mode, sha256=loaded.sha256
        )
        if request.phase not in bundle.phases:
            response = self._config_response(
                request,
                event_id,
                started,
                ErrorInfo(
                    layer=Layer.PIPELINE,
                    type="CONFIG_INVALID",
                    message=(
                        f"phase {request.phase} is not enabled in bundle "
                        f"{bundle.id} v{bundle.version}"
                    ),
                    retryable=False,
                ),
                info,
            )
            self._record(request, response, context)
            return Evaluated(response)

        errors: list[ErrorInfo] = []
        trace: list[TraceEntry] = []
        degraded = False
        decision: Decision | None = None
        l0_result: EntityDetectionResult | None = None

        # ---------------- L0
        l0_started = time.perf_counter()
        text = ""
        # Per-type rules (guardrail policies or legacy on_detect) and the detector configuration
        # for this phase: a policy only screens the roles / phases it is enabled for (ADR-0021).
        rules = type_rules(bundle.l0)
        l0_config = phase_config(bundle.l0, rules, request.phase)
        parts: list[tuple[str, str]] = []
        try:
            parts = extract_target_parts(request.content, target or bundle.l0.target)
            segments = [part for _, part in parts]
            text = SEPARATOR.join(segments)
            l0_result = await asyncio.wait_for(
                self._detector.adetect_segments(
                    segments,
                    tenant_id=request.tenant_id,
                    config=l0_config,
                    language_hint=request.content.language,
                    # inputs are resent every turn; answers and tool calls are checked once
                    cache=request.phase in (Phase.PRE_LLM, Phase.TOOL_OUTPUT),
                ),
                timeout=max(budget_s - (time.perf_counter() - started), 0.001),
            )
        except Exception as exc:  # mapped to an explicit failure below
            kind, retryable = _l0_error_kind(exc)
            errors.append(ErrorInfo(layer=Layer.L0, type=kind, retryable=retryable))
            trace.append(TraceEntry(layer=Layer.L0, outcome="error", latency_ms=_ms(l0_started)))
            decision = failure_decision(bundle.on_evaluation_failure, request.phase, f"L0 {kind}")
            degraded = True
            logger.warning("l0.failed request_id=%s kind=%s", request.request_id, kind)

        # ---------------- L0 gate (ADR-0014, ADR-0036): BLOCK / STEP_UP final, else a floor
        output_modifications: OutputModifications | None = None
        floor: Decision | None = None
        if decision is None and l0_result is not None:
            if l0_result.summary:
                gate = decide_l0(
                    bundle.l0,
                    l0_result,
                    build_facts(request, l0_result),
                    request.phase,
                    text,
                    rules,
                )
                decision = gate.decision
                output_modifications = gate.output_modifications
                outcome = str(decision.action)
            else:
                outcome = "no_entities"
            trace.append(TraceEntry(layer=Layer.L0, outcome=outcome, latency_ms=_ms(l0_started)))

        l0_ms = _ms(l0_started)
        tags = l0_tags(l0_result) if l0_result else []

        # A gate outcome that is not BLOCK / STEP_UP becomes the floor when L1 has work.
        if (
            decision is not None
            and not degraded
            and decision.decided_by == "L0"
            and decision.action not in _TERMINAL_L0
            and l1_has_work(bundle, request)
        ):
            floor, decision = decision, None

        # ---------------- L1
        l1_ms: float | None = None
        l1b_ms: float | None = None
        l1b: L1bResult | None = None
        if decision is None and l1b_applies(bundle, request) and not l1a_has_rules(bundle):
            assert l0_result is not None
            l1b = await self.l1b.run(
                bundle,
                request,
                redacted_parts(parts, l0_result.entities),
                l0_result.summary,
                remaining_s=budget_s - (time.perf_counter() - started),
                event_id=event_id,
                context=context,
            )
            hold = self._held.get(event_id)
            if hold is not None and l1b.background is not None:
                hold.background = l1b.background
                l1b.background.add_done_callback(lambda _: self._release(event_id))
            l1b_ms = l1b.latency_ms
            trace.extend(l1b.trace)
            errors.extend(l1b.errors)
            tags.extend(l1b.tags)
            degraded = degraded or l1b.degraded
            if l1b.failed is not None:
                decision = with_floor(
                    floor,
                    failure_decision(
                        bundle.on_evaluation_failure, request.phase, f"L1b {l1b.failed}"
                    ),
                )
            elif l1b.decision is not None:
                decision = with_floor(floor, l1b.decision)
            else:  # only shadow policies applied, or model calls disabled: as without L1b
                decision = floor or Decision(
                    action=Action.ALLOW,
                    allowed=True,
                    severity=Severity.LOW,
                    reason="no entities detected and no policy rules apply",
                    decided_by="L1a",
                )
        if decision is None:
            l1_started = time.perf_counter()
            if l1_has_work(bundle, request):
                errors.append(
                    ErrorInfo(
                        layer=Layer.L1A,
                        type="L1_NOT_IMPLEMENTED",
                        message=(
                            "bundle has L1 rules or policies; "
                            "this engine version cannot evaluate them"
                        ),
                        retryable=False,
                    )
                )
                trace.append(
                    TraceEntry(
                        layer=Layer.L1A, outcome="not_implemented", latency_ms=_ms(l1_started)
                    )
                )
                decision = with_floor(
                    floor,
                    failure_decision(
                        bundle.on_evaluation_failure, request.phase, "L1_NOT_IMPLEMENTED"
                    ),
                )
                degraded = True
            else:
                trace.append(
                    TraceEntry(layer=Layer.L1A, outcome="no_rules", latency_ms=_ms(l1_started))
                )
                decision = floor or Decision(
                    action=Action.ALLOW,
                    allowed=True,
                    severity=Severity.LOW,
                    reason="no entities detected and no policy rules apply",
                    decided_by="L1a",
                )
            l1_ms = _ms(l1_started)

        # Masking applies only while the final action is the gate's masking outcome.
        if decision.action != Action.ALLOW_WITH_MODIFICATIONS:
            output_modifications = None

        # ---------------- mode
        would_have = None
        if bundle.mode == Mode.MONITOR:
            would_have = decision
            decision = Decision(
                action=Action.ALLOW,
                allowed=True,
                severity=decision.severity,
                reason=f"MONITOR mode (would have: {would_have.action})",
                decided_by=would_have.decided_by,
                matched=would_have.matched,
            )
            output_modifications = None  # MONITOR never changes the caller's content
        assert decision.allowed == (decision.action in ALLOWED_ACTIONS)

        response = EvaluationResponse(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            bundle=info,
            phase=request.phase,
            decision=decision,
            would_have=would_have,
            degraded=degraded,
            tags=tags,
            entities=l0_result.summary if l0_result else [],
            output_modifications=output_modifications,
            event_id=event_id,
            review_queued=False,  # event sink arrives with L2 (F3)
            latency_ms=LatencyMs(total=_ms(started), l0=l0_ms, l1a=l1_ms, l1b=l1b_ms),
            trace=trace if request.flags.trace else None,
            errors=errors,
        )
        self._record(
            request,
            response,
            context,
            floor=floor,
            review=bool(l1b and l1b.review),
            review_reasons=l1b.review_reasons if l1b else None,
            l1b=EventL1b(
                provider=bundle.l1b.provider,
                enforcing=l1b.ran,
                shadow=l1b.shadow,
                failed=l1b.failed,
                latency_ms=l1b.latency_ms,
            )
            if l1b is not None
            else None,
        )
        logger.info(
            "decision request_id=%s event_id=%s bundle=%s@%s action=%s decided_by=%s "
            "degraded=%s total_ms=%s",
            request.request_id,
            event_id,
            bundle.id,
            bundle.version,
            decision.action,
            decision.decided_by,
            degraded,
            response.latency_ms.total,
        )
        return Evaluated(
            response=response,
            scanned_text=text,
            entities=list(l0_result.entities) if l0_result else [],
            masked_types=frozenset(output_modifications.entities)
            if output_modifications
            else frozenset(),
        )

    # ------------------------------------------------------------------ records (ADR-0041)

    def _record(
        self,
        request: EvaluationRequest,
        response: EvaluationResponse,
        context: RecordContext | None,
        **event: object,
    ) -> None:
        try:
            now = datetime.now(UTC)
            self._emit(
                response.event_id,
                context,
                [
                    decision_event(request, response, context, now, **event),
                    evaluation_usage(response, context, now),
                ],
            )
        except Exception:  # a record never changes the decision (ADR-0042)
            logger.exception("records.build_failed event_id=%s", response.event_id)

    def _emit(self, event_id: str, context: RecordContext | None, records: list[Record]) -> None:
        hold = self._held.get(event_id)
        if hold is not None and not hold.committed:
            hold.records.extend(records)
        elif hold is not None or context is None or context.record:
            for record in records:
                self.recorder.record(record)
        # else: a held evaluation already released; its calls have all reported, so not reached

    def _record_l1b(self, report: L1bReport) -> None:
        try:
            now = datetime.now(UTC)
            bundle, request = report.bundle, report.request
            tenant = request.tenant_id or self._store.default_tenant
            outcomes = {o.policy.id: o for o in report.outcomes}
            records: list[Record] = []
            for policy in report.policies:
                records.append(
                    l1b_outcome(
                        event_id=report.event_id,
                        tenant_id=tenant,
                        bundle_id=bundle.id,
                        bundle_version=bundle.version,
                        request=request,
                        policy=policy,
                        provider=bundle.l1b.provider,
                        occurred_at=now,
                        latency_ms=report.latency_ms,
                        outcome=outcomes.get(policy.id),
                        error=report.failed,
                    )
                )
            if report.usage is not None and report.usage.calls:
                for usage in l1b_usage(
                    event_id=report.event_id,
                    tenant_id=tenant,
                    bundle_id=bundle.id,
                    bundle_version=bundle.version,
                    context=report.context,
                    occurred_at=now,
                    calls=report.usage.calls,
                    input_tokens=report.usage.input_tokens,
                    output_tokens=report.usage.output_tokens,
                ):
                    records.append(usage)
            self._emit(report.event_id, report.context, records)
        except Exception:  # a record never changes the decision (ADR-0042)
            logger.exception("records.build_failed event_id=%s", report.event_id)

    def _config_response(
        self,
        request: EvaluationRequest,
        event_id: str,
        started: float,
        error: ErrorInfo,
        info: BundleInfo,
    ) -> EvaluationResponse:
        logger.warning(
            "config_failure request_id=%s bundle=%s type=%s",
            request.request_id,
            info.id,
            error.type,
        )
        return EvaluationResponse(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            bundle=info,
            phase=request.phase,
            decision=config_failure_decision(error.type, error.message or ""),
            degraded=True,
            event_id=event_id,
            latency_ms=LatencyMs(total=_ms(started)),
            errors=[error],
        )


def _l0_error_kind(exc: BaseException) -> tuple[str, bool]:
    if isinstance(exc, TimeoutError):
        return "TIMEOUT", True
    if isinstance(exc, TenantKeyError):
        return "TENANT_KEY_MISSING", False
    return "L0_ERROR", True
