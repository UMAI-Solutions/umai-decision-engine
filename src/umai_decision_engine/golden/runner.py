"""Golden-test runner (ADR-0015, docs/contracts/policy-tests.md).

The selected bundle versions are loaded through the regular ``BundleStore`` (manifest hash and
signature checks, schema and semantic validation) and every case is evaluated by the regular
``Evaluator``: a golden test sees exactly what a caller of ``/v1/evaluate`` would see.

Results carry case ids and decision fields only, never the case input text (AGENTS.md rule 1
holds for test output too).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from umai_decision_engine.contracts.bundle import PolicyBundle
from umai_decision_engine.contracts.common import Phase
from umai_decision_engine.contracts.evaluation import (
    CONTRACT_VERSION,
    BundleRef,
    ChatMessage,
    Content,
    EvaluationRequest,
    EvaluationResponse,
)
from umai_decision_engine.contracts.policy_tests import (
    CaseInput,
    Expectation,
    GoldenCase,
    PolicyTestFile,
)
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.pipeline.orchestrator import Evaluator
from umai_decision_engine.policy.sources import (
    BUNDLE_FILE,
    TENANTS_DIR,
    DirectorySource,
    SnapshotSource,
    SourceSnapshot,
)
from umai_decision_engine.policy.store import BundleStore
from umai_decision_engine.settings import Settings

TESTS_DIR = "tests"
TENANT_ID = "golden-tests"
_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)
_HMAC_KEY = b"umai-de-golden-tests"  # value hashes are never asserted


# ------------------------------------------------------------------ results


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    file: str  # relative to the version directory, e.g. "tests/l0-gate.yaml"
    failures: tuple[str, ...] = ()
    # What the pipeline decided (decision fields only, never the input text); lets a reviewer
    # accept a changed behaviour as the new expectation (policy service, ADR-0022).
    actual: dict | None = None

    @property
    def passed(self) -> bool:
        return not self.failures


@dataclass
class VersionReport:
    bundle_id: str
    version: int
    active: bool
    has_tests: bool = False
    errors: list[str] = field(default_factory=list)  # bundle load or test file errors
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.bundle_id} v{self.version}" + (" (active)" if self.active else "")

    @property
    def passed(self) -> bool:
        return not self.errors and all(c.passed for c in self.cases)


@dataclass
class RunReport:
    versions: list[VersionReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # layout, active pointers

    def untested_active(self) -> list[VersionReport]:
        return [v for v in self.versions if v.active and not v.has_tests]

    def ok(self, *, require_tests: bool = False) -> bool:
        if self.errors or not all(v.passed for v in self.versions):
            return False
        return not (require_tests and self.untested_active())

    @property
    def cases(self) -> list[CaseResult]:
        return [c for v in self.versions for c in v.cases]


# ------------------------------------------------------------------ selection


def _select(path: Path) -> tuple[Path, str | None, int | None]:
    """``path`` is a policies root, a bundle directory or a version directory."""

    if (path / BUNDLE_FILE).is_file():
        if not path.name.isdigit():
            raise ValueError(f"{path}: a version directory must be named by its version number")
        return path.parent.parent, path.parent.name, int(path.name)
    if not path.is_dir():
        raise FileNotFoundError(f"{path}: not a directory")
    if any(child.is_dir() and child.name.isdigit() for child in path.iterdir()):
        return path.parent, path.name, None
    return path, None, None


def _within(location: str, scope: Path) -> bool:
    return Path(location).resolve().is_relative_to(scope.resolve())


def _selected(
    snapshot: SourceSnapshot, scope: Path, bundle_id: str | None, version: int | None
) -> SourceSnapshot:
    def wanted(bid: str, ver: int | None = None) -> bool:
        if bundle_id is not None and bid != bundle_id:
            return False
        return version is None or ver is None or ver == version

    return SourceSnapshot(
        artifacts=[
            a
            for a in snapshot.artifacts
            if wanted(a.bundle_id, a.version) and _within(a.location, scope)
        ],
        # a single version is tested on its own; its bundle's pointer is not in scope
        active={}
        if version is not None
        else {key: v for key, v in snapshot.active.items() if wanted(key[1])},
        errors=[(loc, msg) for loc, msg in snapshot.errors if _within(loc, scope)],
    )


# ------------------------------------------------------------------ cases


@dataclass(frozen=True)
class _Planned:
    file: str
    case: GoldenCase
    input: CaseInput


def _short(exc: Exception) -> str:
    # Never str(ValidationError): it echoes input values, which may be case text.
    if isinstance(exc, ValidationError):
        parts = [
            f"{'.'.join(str(p) for p in e['loc']) or '<file>'}: {e['msg']}"
            for e in exc.errors()[:5]
        ]
        return "; ".join(parts)
    if isinstance(exc, yaml.YAMLError):
        return f"invalid YAML ({type(exc).__name__})"
    return str(exc)[:300]


def load_cases(version_dir: Path, bundle: PolicyBundle) -> tuple[list[_Planned], list[str], bool]:
    """Parse ``tests/*.yaml``; returns (cases, errors, has_tests)."""

    tests_dir = version_dir / TESTS_DIR
    files = (
        sorted(p for p in tests_dir.iterdir() if p.is_file() and p.suffix in {".yaml", ".yml"})
        if tests_dir.is_dir()
        else []
    )
    enabled = set(bundle.l0.entities) | {r.entity for r in bundle.l0.custom_recognizers}
    planned: list[_Planned] = []
    errors: list[str] = []
    seen: dict[str, str] = {}

    for path in files:
        rel = path.relative_to(version_dir).as_posix()
        try:
            data = yaml.safe_load(path.read_text("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("expected a mapping with 'contract_version' and 'cases'")
            test_file = PolicyTestFile.model_validate(data)
        except (yaml.YAMLError, ValidationError, ValueError) as exc:
            errors.append(f"{rel}: {_short(exc)}")
            continue
        for case in test_file.cases:
            where = f"{rel}: case {case.id}"
            if case.id in seen:
                errors.append(f"{where}: duplicate case id (also in {seen[case.id]})")
                continue
            seen[case.id] = rel
            try:
                merged = case.input.merged_over(test_file.defaults)
            except (ValidationError, ValueError) as exc:
                errors.append(f"{where}: {_short(exc)}")
                continue
            unknown = sorted(case.expect.entity_types() - enabled)
            if unknown:
                errors.append(
                    f"{where}: entity types not enabled in the bundle: {', '.join(unknown)}"
                )
                continue
            planned.append(_Planned(rel, case, merged))
    return planned, errors, bool(files)


def build_request(
    case_id: str,
    data: CaseInput,
    bundle_id: str,
    version: int,
    tenant_id: str = TENANT_ID,
) -> EvaluationRequest:
    focus = data.focus or "LAST_USER_MESSAGE"
    if data.messages is not None:
        messages = data.messages
    else:
        role = "assistant" if focus == "LAST_ASSISTANT_MESSAGE" else "user"
        messages = [ChatMessage(role=role, content=data.text or "")]
    optional = {
        name: value
        for name, value in (("subject", data.subject), ("destination", data.destination))
        if value is not None
    }
    return EvaluationRequest(
        contract_version=CONTRACT_VERSION,
        request_id=f"golden:{case_id}"[:128],
        timestamp=_TIMESTAMP,
        tenant_id=tenant_id,
        bundle=BundleRef(id=bundle_id, version=version),
        phase=data.phase or Phase.PRE_LLM,
        content=Content(
            messages=messages,
            focus=focus,
            artifacts=data.artifacts or [],
            language=data.language,
        ),
        action=data.action,
        **optional,
    )


def _fmt(values: Iterable[object]) -> str:
    return "[" + ", ".join(sorted(str(v) for v in values)) + "]"


def observed(response: EvaluationResponse) -> dict:
    """The decision fields a golden case can assert, as plain data."""

    mods = response.output_modifications
    return {
        "action": str(response.decision.action),
        "decided_by": response.decision.decided_by,
        "severity": str(response.decision.severity),
        "reason": response.decision.reason,
        "entities": sorted(e.type for e in response.entities),
        "masked": sorted(mods.entities) if mods else [],
        "redacted_text": mods.redacted_text if mods else None,
        "matched": [m.id for m in response.decision.matched],
        "errors": sorted(e.type for e in response.errors),
        "degraded": response.degraded,
        "would_have": str(response.would_have.action) if response.would_have else None,
    }


def compare(expect: Expectation, response: EvaluationResponse) -> list[str]:
    """Mismatches between ``expect`` and ``response``; empty when the case passes."""

    failures: list[str] = []
    decision = response.decision
    mods = response.output_modifications

    def check(name: str, expected: object, got: object) -> None:
        if expected is not None and expected != got:
            failures.append(f"{name}: expected {expected}, got {got}")

    def check_set(name: str, expected: list[str] | None, got: Iterable[str]) -> None:
        got_set = set(got)
        if expected is not None and set(expected) != got_set:
            failures.append(f"{name}: expected {_fmt(expected)}, got {_fmt(got_set)}")

    check("action", expect.action, decision.action)
    if expect.acceptable is not None and decision.action not in expect.acceptable:
        failures.append(f"action: expected one of {_fmt(expect.acceptable)}, got {decision.action}")
    check("decided_by", expect.decided_by, decision.decided_by)
    check("severity", expect.severity, decision.severity)
    check_set("entities", expect.entities, (e.type for e in response.entities))
    check_set("masked", expect.masked, mods.entities if mods else [])
    if expect.redacted_text is not None:
        if mods is None:
            failures.append("redacted_text: expected a masked text, got no output modifications")
        elif mods.redacted_text != expect.redacted_text:
            failures.append(
                f"redacted_text: expected {expect.redacted_text!r}, got {mods.redacted_text!r}"
            )
    if expect.reason_contains is not None and expect.reason_contains not in decision.reason:
        failures.append(
            f"reason: expected to contain {expect.reason_contains!r}, got {decision.reason!r}"
        )
    if expect.would_have is not None:
        would = response.would_have.action if response.would_have else "none (not MONITOR mode)"
        check("would_have", expect.would_have, would)
    check_set("errors", expect.errors, (e.type for e in response.errors))
    check_set("matched", expect.matched, (m.id for m in decision.matched))
    check("degraded", expect.degraded, response.degraded)
    return failures


# ------------------------------------------------------------------ run


def _settings() -> Settings:
    # Explicit values, no .env: a developer's local settings must not change test outcomes.
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        max_timeout_ms=60_000,
        default_timeout_ms=30_000,  # golden cases check decisions, not latency
        require_signed_bundles=False,
        allow_inline_bundles=False,
        expose_detect_endpoint=False,
    )


async def run_golden(
    path: Path,
    *,
    detector: EntityDetector | None = None,
    public_keys: dict[str, str] | None = None,
) -> RunReport:
    """Validate every bundle version under ``path`` and run the golden cases it carries."""

    path = Path(path)
    root, bundle_id, version = _select(path)
    snapshot = DirectorySource(root).scan()
    selected = _selected(snapshot, path, bundle_id, version)
    report = RunReport()
    if not selected.artifacts and not selected.errors:
        report.errors.append(f"{path}: no bundle versions found")
        return report

    # Scanning inside policies/tenants/<tenant>/ sees that tenant's bundles as unowned; their
    # manifests name the tenant, so it becomes the store's default owner for this run.
    owner = root.name if root.parent.name == TENANTS_DIR else "default"
    store = BundleStore(
        SnapshotSource(selected),
        public_keys=public_keys or {},
        require_signed=False,
        default_tenant=owner,
    )
    store.reload(force=True)
    state = store.state
    locations = {a.location for a in selected.artifacts}
    report.errors.extend(
        f"{e.location}: {e.error}" for e in state.errors if e.location not in locations
    )

    # (report entry, cases, owning tenant, bundle id): cases run as the bundle's owner, the only
    # tenant that can resolve it (ADR-0019 §2)
    plans: list[tuple[VersionReport, list[_Planned], str, str]] = []
    for artifact in sorted(
        selected.artifacts, key=lambda a: (a.tenant_id or "", a.bundle_id, a.version)
    ):
        entry = VersionReport(
            bundle_id=artifact.bundle_id
            if artifact.tenant_id is None
            else f"{artifact.tenant_id}/{artifact.bundle_id}",
            version=artifact.version,
            active=snapshot.active.get((artifact.tenant_id, artifact.bundle_id))
            == artifact.version,
        )
        report.versions.append(entry)
        entry.errors.extend(e.error for e in state.errors if e.location == artifact.location)
        loaded = state.bundles.get((store.owner(artifact), artifact.bundle_id, artifact.version))
        if loaded is None:
            continue
        cases, errors, entry.has_tests = load_cases(Path(artifact.location), loaded.bundle)
        entry.errors.extend(errors)
        plans.append((entry, cases, store.owner(artifact), artifact.bundle_id))

    if not any(cases for _, cases, _, _ in plans):
        return report

    own_detector = detector is None
    if detector is None:
        detector = EntityDetector(lambda _tenant: _HMAC_KEY, threads=2)
        await asyncio.to_thread(detector.warm_up)
    evaluator = Evaluator(store, detector, _settings())
    try:
        for entry, cases, owner, bundle_id in plans:
            for item in cases:
                request = build_request(
                    item.case.id, item.input, bundle_id, entry.version, tenant_id=owner
                )
                actual = None
                try:
                    response = await evaluator.evaluate(request)
                except Exception as exc:  # reported as a failed case, never swallowed
                    failures = [f"evaluation raised {type(exc).__name__}: {str(exc)[:200]}"]
                else:
                    failures = compare(item.case.expect, response)
                    actual = observed(response)
                entry.cases.append(CaseResult(item.case.id, item.file, tuple(failures), actual))
    finally:
        if own_detector:
            detector.close()
    return report


def format_report(report: RunReport, *, require_tests: bool = False) -> list[str]:
    lines: list[str] = []
    for entry in report.versions:
        if entry.cases or entry.errors:
            lines.append(f"{entry.label}: {len(entry.cases)} cases")
        else:
            lines.append(f"{entry.label}: no golden tests")
        for error in entry.errors:
            lines.append(f"  error {error}")
        for case in entry.cases:
            if case.passed:
                lines.append(f"  ok    {case.case_id}")
                continue
            lines.append(f"  FAIL  {case.case_id}  [{case.file}]")
            lines.extend(f"        {failure}" for failure in case.failures)
    lines.extend(f"error: {error}" for error in report.errors)
    if require_tests:
        lines.extend(
            f"error: {entry.label} has no golden tests (--require-tests)"
            for entry in report.untested_active()
        )
    cases = report.cases
    failed = sum(not c.passed for c in cases)
    errors = len(report.errors) + sum(len(v.errors) for v in report.versions)
    summary = (
        f"{len(report.versions)} versions, {len(cases)} cases: "
        f"{len(cases) - failed} passed, {failed} failed"
    )
    lines.append(summary + (f", {errors} errors" if errors else ""))
    return lines
