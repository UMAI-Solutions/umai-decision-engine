"""Draft → validate → test → publish → activate (docs/architecture/policy-lifecycle.md, ADR-0022).

Validation and tests use the engine's own code (schema, ``validate_bundle``, the golden-test
runner through the real pipeline), so a draft that passes here loads and behaves the same in
the engine. Golden cases travel with the guardrail: a publish copies the base version's
``tests/``. When a change alters a case's outcome on purpose, the reviewer accepts the new
behaviour and the case's expectation is rewritten from the observed decision.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

import yaml
from pydantic import ValidationError

from umai_decision_engine.contracts.bundle import ALL_ROLES, PolicyBundle
from umai_decision_engine.control_plane.drafts import Draft, DraftStore
from umai_decision_engine.control_plane.repository import (
    TESTS_DIR,
    GuardrailRepository,
    bundle_to_data,
    bundle_to_yaml,
)
from umai_decision_engine.golden.runner import RunReport, run_golden
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.policy.sources import BUNDLE_FILE
from umai_decision_engine.policy.validate import validate_bundle


class ServiceError(Exception):
    def __init__(self, status: int, message: str, details: object = None) -> None:
        super().__init__(message)
        self.status = status
        self.details = details


def to_policy_form(data: dict) -> dict:
    """A bundle ≤ 0.5 (``entities`` + ``on_detect``) as guardrail policies (0.6+), one policy per
    action, so it can be edited like any guardrail. Bundles with policies are returned as is."""

    l0 = dict(data["l0"])
    if l0.get("policies"):
        return data
    on_detect = l0.pop("on_detect", {}) or {}
    default = on_detect.get("default", "BLOCK")
    actions = on_detect.get("actions", {}) or {}
    groups: dict[str, list[str]] = {}
    for entity in l0.pop("entities", []):
        groups.setdefault(actions.get(entity, default), []).append(entity)
    names = {
        "BLOCK": ("blocked-data", "Engellenen veriler"),
        "ALLOW_WITH_MODIFICATIONS": ("masked-data", "Maskelenen veriler"),
        "STEP_UP_APPROVAL": ("approval-data", "Onay gerektiren veriler"),
    }
    l0["policies"] = [
        {
            "id": names[action][0],
            "version": 1,
            "name": names[action][1],
            "entities": entities,
            "action": action,
            "severity": on_detect.get("severity", "HIGH"),
            "roles": list(ALL_ROLES),
            "sensitivity": "L3",
            "enabled": True,
        }
        for action, entities in groups.items()
    ]
    return {**data, "l0": l0}


def problems_of(data: dict) -> tuple[PolicyBundle | None, list[str]]:
    try:
        bundle = PolicyBundle.model_validate(data)
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in e['loc']) or 'bundle'}: {e['msg']}"
            for e in exc.errors()[:20]
        ]
    return bundle, validate_bundle(bundle)


def report_to_data(report: RunReport) -> dict:
    cases = [
        {**asdict(case), "failures": list(case.failures), "passed": case.passed}
        for case in report.cases
    ]
    versions = report.versions
    return {
        "passed": report.ok(),
        "has_tests": any(v.has_tests for v in versions),
        "errors": report.errors + [e for v in versions for e in v.errors],
        "cases": cases,
        "failed": sum(not c["passed"] for c in cases),
        "total": len(cases),
    }


_EXPECT_FROM_ACTUAL = (
    "action",
    "decided_by",
    "severity",
    "entities",
    "masked",
    "matched",
    "errors",
    "degraded",
    "would_have",
)


def _accepted_expectation(old: dict, actual: dict) -> dict:
    """The case's expectation rewritten from what the pipeline now decides, keeping the same
    fields the author chose to assert."""

    new: dict = {}
    for key, value in old.items():
        if key in _EXPECT_FROM_ACTUAL:
            if key == "would_have" and actual["would_have"] is None:
                continue
            new[key] = actual[key]
        elif key == "acceptable":
            new["action"] = actual["action"]
        elif key == "redacted_text":
            if actual["redacted_text"] is not None:
                new[key] = actual["redacted_text"]
        elif key == "reason_contains":
            new[key] = value if value in actual["reason"] else actual["reason"]
        else:
            new[key] = value
    new.setdefault("action", actual["action"])
    return new


def accept_cases(tests_dir: Path, accepted: dict[str, tuple[str, dict]]) -> None:
    """Rewrite the expectations of accepted cases (case id → (file, observed decision))."""

    for file in sorted({file for file, _ in accepted.values()}):
        path = tests_dir.parent / file
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for case in data["cases"]:
            if case["id"] in accepted:
                case["expect"] = _accepted_expectation(case["expect"], accepted[case["id"]][1])
        header = "# Expectations of some cases were accepted from a reviewed change (ADR-0022).\n"
        body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
        path.write_text(header + body, encoding="utf-8", newline="\n")


class PolicyService:
    def __init__(
        self,
        repository: GuardrailRepository,
        drafts: DraftStore,
        detector: EntityDetector,
        signing_key: tuple[str, str] | None = None,
    ) -> None:
        self.repository = repository
        self.drafts = drafts
        self.detector = detector
        self.signing_key = signing_key

    # ----------------------------------------------------------- guardrails

    def summary(self, bundle_id: str) -> dict:
        history = self.repository.changelog(bundle_id)
        published = {e["version"]: e for e in history if e.get("action") == "publish"}
        versions = []
        for version in self.repository.versions(bundle_id):
            try:
                bundle = self.repository.load(bundle_id, version)
                manifest = self.repository.manifest(bundle_id, version)
                entry = published.get(version, {})
                versions.append(
                    {
                        "version": version,
                        "name": bundle.name,
                        "mode": bundle.mode,
                        "contract_version": bundle.contract_version,
                        "policies": len(bundle.l0.policies),
                        "signed": bool(manifest and manifest.signature),
                        "published_at": entry.get("at"),
                        "base_version": entry.get("base_version"),
                        "note": entry.get("note"),
                        "valid": True,
                    }
                )
            except Exception as exc:  # an invalid version is listed, not hidden
                versions.append({"version": version, "valid": False, "error": str(exc)[:300]})
        if not versions:
            raise ServiceError(404, f"guardrail {bundle_id} does not exist")
        draft = self.drafts.get(bundle_id)
        return {
            "id": bundle_id,
            "active_version": self.repository.active(bundle_id),
            "versions": versions,
            "draft": self.draft_view(draft) if draft else None,
            "history": history[-50:],
        }

    def list(self) -> list[dict]:
        drafts = self.drafts.ids()
        out = []
        for bundle_id in self.repository.ids():
            active = self.repository.active(bundle_id)
            live = None
            if active:
                try:
                    live = self.repository.load(bundle_id, active)
                except Exception:  # listed without details
                    live = None
            policies = live.l0.policies if live else []
            history = self.repository.changelog(bundle_id)
            out.append(
                {
                    "id": bundle_id,
                    "name": live.name if live else None,
                    "description": live.description if live else None,
                    "mode": live.mode if live else None,
                    "active_version": active,
                    "versions": self.repository.versions(bundle_id),
                    "has_draft": bundle_id in drafts,
                    # older bundles have no policies: count their detected types instead
                    "policies": len(policies),
                    "policies_on": sum(p.enabled for p in policies),
                    "detectors": len(live.l0.entities) if live else 0,
                    "last_change": history[-1] if history else None,
                }
            )
        return out

    # ----------------------------------------------------------- drafts

    def draft_view(self, draft: Draft) -> dict:
        _, problems = problems_of(draft.bundle)
        return {
            "bundle_id": draft.bundle_id,
            "base_version": draft.base_version,
            "bundle": draft.bundle,
            "problems": problems,
            "created_at": draft.created_at,
            "updated_at": draft.updated_at,
        }

    def get_draft(self, bundle_id: str) -> dict:
        draft = self.drafts.get(bundle_id)
        if draft is None:
            raise ServiceError(404, f"guardrail {bundle_id} has no draft")
        return self.draft_view(draft)

    def create_draft(self, bundle_id: str, base_version: int | None) -> dict:
        if self.drafts.get(bundle_id) is not None:
            raise ServiceError(409, f"guardrail {bundle_id} already has a draft")
        version = base_version or self.repository.active(bundle_id)
        if version is None:
            raise ServiceError(404, f"guardrail {bundle_id} has no active version")
        try:
            bundle = self.repository.load(bundle_id, version)
        except Exception as exc:
            raise ServiceError(404, str(exc)) from exc
        data = to_policy_form(bundle_to_data(bundle))
        return self.draft_view(self.drafts.create(bundle_id, version, data))

    def save_draft(self, bundle_id: str, data: dict) -> dict:
        if not isinstance(data, dict) or data.get("id") != bundle_id:
            raise ServiceError(400, "the draft must be a bundle object with the guardrail's id")
        draft = self.drafts.save(bundle_id, data)  # saved even when invalid: no lost work
        if draft is None:
            raise ServiceError(404, f"guardrail {bundle_id} has no draft")
        return self.draft_view(draft)

    def discard_draft(self, bundle_id: str) -> None:
        if not self.drafts.delete(bundle_id):
            raise ServiceError(404, f"guardrail {bundle_id} has no draft")

    # ----------------------------------------------------------- test and publish

    def _valid_draft(self, bundle_id: str) -> tuple[Draft, PolicyBundle]:
        draft = self.drafts.get(bundle_id)
        if draft is None:
            raise ServiceError(404, f"guardrail {bundle_id} has no draft")
        bundle, problems = problems_of(draft.bundle)
        if bundle is None or problems:
            raise ServiceError(422, "the draft is not valid", {"problems": problems})
        return draft, bundle

    async def _run_tests(self, draft: Draft, bundle: PolicyBundle, workdir: Path) -> RunReport:
        version_dir = workdir / bundle.id / str(draft.base_version)
        version_dir.mkdir(parents=True)
        (version_dir / BUNDLE_FILE).write_bytes(
            bundle_to_yaml(bundle.model_copy(update={"version": draft.base_version}), "")
        )
        base_tests = self.repository.version_dir(bundle.id, draft.base_version) / TESTS_DIR
        if base_tests.is_dir():
            shutil.copytree(base_tests, version_dir / TESTS_DIR)
        return await run_golden(version_dir, detector=self.detector)

    async def test_draft(self, bundle_id: str) -> dict:
        draft, bundle = self._valid_draft(bundle_id)
        with tempfile.TemporaryDirectory(prefix="umai-draft-") as tmp:
            report = await self._run_tests(draft, bundle, Path(tmp))
        return report_to_data(report)

    async def publish(
        self, bundle_id: str, *, activate: bool, accept: list[str], note: str | None
    ) -> dict:
        draft, bundle = self._valid_draft(bundle_id)
        with tempfile.TemporaryDirectory(prefix="umai-draft-") as tmp:
            report = await self._run_tests(draft, bundle, Path(tmp))
            failing = {c.case_id: c for c in report.cases if not c.passed}
            unaccepted = sorted(set(failing) - set(accept))
            if report.errors or unaccepted:
                raise ServiceError(
                    422,
                    "golden tests fail; fix the draft or accept the new behaviour of these cases",
                    {"unaccepted": unaccepted, "report": report_to_data(report)},
                )
            tests_dir = Path(tmp) / bundle_id / str(draft.base_version) / TESTS_DIR
            accepted = {
                cid: (c.file, c.actual) for cid, c in failing.items() if c.actual is not None
            }
            if accepted:
                accept_cases(tests_dir, accepted)
            version = self.repository.publish(
                bundle,
                base_version=draft.base_version,
                tests_dir=tests_dir,
                note=note,
                signing_key=self.signing_key,
            )
        previous = self.repository.activate(bundle_id, version, note) if activate else None
        self.drafts.delete(bundle_id)
        return {
            "version": version,
            "activated": activate,
            "previous_active": previous,
            "accepted": sorted(accepted),
            "tests": {"total": len(report.cases)},
        }

    def activate(self, bundle_id: str, version: int, note: str | None) -> dict:
        try:
            previous = self.repository.activate(bundle_id, version, note)
        except Exception as exc:
            raise ServiceError(422, str(exc)) from exc
        return {"active_version": version, "previous_active": previous}
