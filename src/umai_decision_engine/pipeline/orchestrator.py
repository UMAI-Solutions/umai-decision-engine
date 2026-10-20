"""Runtime evaluation: resolve bundle → L0 → (L0 gate | L1) → final decision.

- L0 detected an entity → the L0 gate decides and L1 does not run (ADR-0014): no decision-model
  call, no tokens.
- L0 detected nothing → L1. L1 (rules and decision model) is not implemented yet: a bundle with
  nothing for L1 to evaluate yields the L1a floor (ALLOW); a bundle with L1 logic is answered
  with its ``on_evaluation_failure`` and an explicit ``L1_NOT_IMPLEMENTED`` error — never a
  silent allow (AGENTS.md rule 4).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from umai_decision_engine.contracts.bundle import PolicyBundle, TargetScope
from umai_decision_engine.contracts.common import (
    ALLOWED_ACTIONS,
    Action,
    Layer,
    Mode,
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
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.l0.hashing import TenantKeyError
from umai_decision_engine.l0.recognizers.catalog import TAG_BY_ENTITY
from umai_decision_engine.l0.target import SEPARATOR, extract_target_segments
from umai_decision_engine.pipeline.failure import config_failure_decision, failure_decision
from umai_decision_engine.pipeline.l0_gate import decide_l0
from umai_decision_engine.pipeline.l0_policies import phase_config, type_rules
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


def l1_has_work(bundle: PolicyBundle, request: EvaluationRequest) -> bool:
    if bundle.l1a.rules:
        return True
    if bundle.l1a.legacy_preflight and bundle.l1a.legacy_preflight.rules:
        return True
    return bundle.l1b.enabled and any(request.phase in p.phases for p in bundle.l1b.policies)


@dataclass(frozen=True)
class Evaluated:
    """An evaluation plus what in-process raw-content callers (the gateway, ADR-0035) need to
    apply it: the scanned text, L0's entity spans over it, and the types the decision masks.
    Never serialized: spans index raw text."""

    response: EvaluationResponse
    scanned_text: str = ""
    entities: list[DetectedEntity] = field(default_factory=list)
    masked_types: frozenset[str] = frozenset()


class Evaluator:
    def __init__(self, store: BundleStore, detector: EntityDetector, settings: Settings) -> None:
        self._store = store
        self._detector = detector
        self._settings = settings

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

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        return (await self.evaluate_detailed(request)).response

    async def evaluate_detailed(
        self, request: EvaluationRequest, *, target: TargetScope | None = None
    ) -> Evaluated:
        """``target`` overrides the bundle's ``l0.target`` (the gateway scans every message it
        forwards, gateway-api 0.2); callers of the HTTP API never set it."""

        started = time.perf_counter()
        event_id = new_event_id()
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
            return Evaluated(
                self._config_response(
                    request,
                    event_id,
                    started,
                    missing,
                    BundleInfo(id=request.bundle.id, version=request.bundle.version),
                )
            )

        bundle = loaded.bundle
        info = BundleInfo(
            id=bundle.id, version=bundle.version, mode=bundle.mode, sha256=loaded.sha256
        )
        if request.phase not in bundle.phases:
            return Evaluated(
                self._config_response(
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
            )

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
        try:
            segments = extract_target_segments(request.content, target or bundle.l0.target)
            text = SEPARATOR.join(segments)
            l0_result = await asyncio.wait_for(
                self._detector.adetect_segments(
                    segments,
                    tenant_id=request.tenant_id,
                    config=l0_config,
                    language_hint=request.content.language,
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

        # ---------------- L0 gate (ADR-0014): a detection is terminal, L1 does not run
        output_modifications: OutputModifications | None = None
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

        # ---------------- L1 (only when L0 detected nothing; not implemented yet)
        l1_ms: float | None = None
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
                decision = failure_decision(
                    bundle.on_evaluation_failure, request.phase, "L1_NOT_IMPLEMENTED"
                )
                degraded = True
            else:
                trace.append(
                    TraceEntry(layer=Layer.L1A, outcome="no_rules", latency_ms=_ms(l1_started))
                )
                decision = Decision(
                    action=Action.ALLOW,
                    allowed=True,
                    severity=Severity.LOW,
                    reason="no entities detected and no policy rules apply",
                    decided_by="L1a",
                )
            l1_ms = _ms(l1_started)

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
            latency_ms=LatencyMs(total=_ms(started), l0=l0_ms, l1a=l1_ms),
            trace=trace if request.flags.trace else None,
            errors=errors,
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
