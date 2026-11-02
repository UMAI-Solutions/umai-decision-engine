"""Draft → validate → test → publish → activate (docs/architecture/policy-lifecycle.md, ADR-0022).

Validation and tests use the engine's own code (schema, ``validate_bundle``, the golden-test
runner through the real pipeline), so a draft that passes here loads and behaves the same in
the engine. Golden cases travel with the guardrail: a publish copies the base version's
``tests/``. When a change alters a case's outcome on purpose, the reviewer accepts the new
behaviour and the case's expectation is rewritten from the observed decision.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import tempfile
import unicodedata
from dataclasses import asdict
from pathlib import Path

import yaml
from pydantic import ValidationError

from umai_decision_engine.contracts.bundle import ALL_ROLES, PolicyBundle
from umai_decision_engine.control_plane.repository import (
    TESTS_DIR,
    GuardrailRepositories,
    GuardrailRepository,
    bundle_to_data,
    bundle_to_yaml,
)
from umai_decision_engine.db.repositories import (
    Conflict,
    DraftInfo,
    DraftRepository,
    StaleRevision,
    TenantInfo,
)
from umai_decision_engine.golden.runner import RunReport, run_golden
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.policy.sources import BUNDLE_FILE
from umai_decision_engine.policy.validate import validate_bundle

log = logging.getLogger("umai.control_plane.service")

BUNDLE_ID = re.compile(r"^[a-z0-9][a-z0-9._\-]{0,127}$")  # policy-bundle `id`
_TEST_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,99}\.ya?ml$")
_TURKISH = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


class ServiceError(Exception):
    """``code`` set: a guardrails 0.1 error, answered as ``{"detail": {"error": code, …}}``
    with ``details`` merged in; otherwise the older ``{"detail", "details"}`` body."""

    def __init__(
        self, status: int, message: str, details: object = None, *, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.details = details
        self.code = code


def slug_of(name: str) -> str:
    """``gr-`` and an ASCII slug of a guardrail's name (Turkish letters transliterated)."""

    ascii_name = unicodedata.normalize("NFKD", name.translate(_TURKISH))
    ascii_name = ascii_name.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:60].strip("-")
    return f"gr-{slug or 'guardrail'}"


def draft_sha256(bundle: dict) -> str:
    """The draft's identity (guardrails 0.1 ``sha256``): canonical JSON of its bundle."""

    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_tests(directory: Path) -> list[dict]:
    """A version's ``tests/`` as a draft's case files: each file's content plus its ``file``."""

    if not directory.is_dir():
        return []
    files = []
    for path in sorted(p for p in directory.iterdir() if p.is_file() and _TEST_FILE.match(p.name)):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        files.append({"file": f"{TESTS_DIR}/{path.name}", **data})
    return files


def write_tests(tests: list[dict], directory: Path) -> None:
    """A draft's case files as a ``tests/`` directory; only plain file names inside it."""

    directory.mkdir(parents=True, exist_ok=True)
    for entry in tests:
        name = str(entry.get("file", "")).removeprefix(f"{TESTS_DIR}/")
        if not _TEST_FILE.match(name):
            raise ServiceError(422, f"invalid test file name {name!r}", code="invalid_case")
        data = {k: v for k, v in entry.items() if k != "file"}
        body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
        (directory / name).write_text(body, encoding="utf-8", newline="\n")


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
        guardrails: GuardrailRepositories,
        drafts: DraftRepository,
        detector: EntityDetector,
        signing_key: tuple[str, str] | None = None,
        *,
        catalog_tenant: str | None = None,
        templates: list[str] | None = None,
    ) -> None:
        self.guardrails = guardrails
        self.drafts = drafts
        self.detector = detector
        self.signing_key = signing_key
        # Guardrail templates (ADR-0056 §3): bundles of the catalog tenant, in display order
        self.catalog_tenant = catalog_tenant or guardrails.default_tenant
        self.templates = list(templates or [])

    # ----------------------------------------------------------- guardrails

    def _own_ids(self, tenant: TenantInfo, repository: GuardrailRepository) -> list[str]:
        """The tenant's guardrails with a version; in the catalog tenant, not its templates."""

        ids = repository.ids()
        if tenant.slug == self.catalog_tenant:
            ids = [i for i in ids if i not in self.templates]
        return ids

    def bindable(self, tenant: TenantInfo) -> set[str]:
        """Guardrails an application may name: published and not archived."""

        repository = self.guardrails.for_tenant(tenant.slug)
        return {i for i in self._own_ids(tenant, repository) if not repository.archived(i)}

    async def count(self, tenant: TenantInfo) -> int:
        """What ``max_guardrails`` counts: guardrails with a version or a draft, not archived."""

        unpublished = await self.drafts.unpublished(tenant)
        return len(self.bindable(tenant)) + len(unpublished)

    async def summary(self, tenant: TenantInfo, bundle_id: str) -> dict:
        repository = self.guardrails.for_tenant(tenant.slug)
        if not repository.versions(bundle_id):
            draft = await self.drafts.get(tenant, bundle_id)
            if draft is None or draft.base_version is not None:
                raise ServiceError(404, f"guardrail {bundle_id} does not exist")
            return {  # created in the console, never published
                "id": bundle_id,
                "active_version": None,
                "versions": [],
                "archived": False,
                "created_from": draft.created_from,
                "draft": self.draft_view(draft),
                "history": [],
            }
        history = repository.changelog(bundle_id)
        published = {e["version"]: e for e in history if e.get("action") == "publish"}
        versions = []
        for version in repository.versions(bundle_id):
            try:
                bundle = repository.load(bundle_id, version)
                manifest = repository.manifest(bundle_id, version)
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
        draft = await self.drafts.get(tenant, bundle_id)
        created = next((e for e in history if e.get("action") == "create"), None)
        return {
            "id": bundle_id,
            "active_version": repository.active(bundle_id),
            "versions": versions,
            "archived": repository.is_archived(history),
            "created_from": created["from"] if created else None,
            "draft": await self._draft_view(tenant, draft) if draft else None,
            "history": history[-50:],
        }

    async def list(self, tenant: TenantInfo) -> list[dict]:
        repository = self.guardrails.for_tenant(tenant.slug)
        drafts = await self.drafts.ids(tenant)
        out = []
        for bundle_id in self._own_ids(tenant, repository):
            history = repository.changelog(bundle_id)
            if repository.is_archived(history):
                continue
            active = repository.active(bundle_id)
            live = None
            if active:
                try:
                    live = repository.load(bundle_id, active)
                except Exception:  # listed without details
                    live = None
            policies = live.l0.policies if live else []
            created = next((e for e in history if e.get("action") == "create"), None)
            out.append(
                {
                    "id": bundle_id,
                    "name": live.name if live else None,
                    "description": live.description if live else None,
                    "mode": live.mode if live else None,
                    "active_version": active,
                    "versions": repository.versions(bundle_id),
                    "has_draft": bundle_id in drafts,
                    # older bundles have no policies: count their detected types instead
                    "policies": len(policies),
                    "policies_on": sum(p.enabled for p in policies),
                    "detectors": len(live.l0.entities) if live else 0,
                    "last_change": history[-1] if history else None,
                    "created_from": created["from"] if created else None,
                }
            )
        for draft in await self.drafts.unpublished(tenant):  # created, never published
            policies = (draft.bundle.get("l0") or {}).get("policies") or []
            out.append(
                {
                    "id": draft.bundle_id,
                    "name": draft.bundle.get("name"),
                    "description": draft.bundle.get("description"),
                    "mode": draft.bundle.get("mode"),
                    "active_version": None,
                    "versions": [],
                    "has_draft": True,
                    "policies": len(policies),
                    "policies_on": sum(bool(p.get("enabled", True)) for p in policies),
                    "detectors": len({e for p in policies for e in p.get("entities") or []}),
                    "last_change": None,
                    "created_from": draft.created_from,
                }
            )
        return out

    # ----------------------------------------------------------- templates (ADR-0056 §3)

    def template_list(self) -> list[dict]:
        catalog = self.guardrails.for_tenant(self.catalog_tenant)
        out = []
        for template_id in self.templates:
            version = catalog.active(template_id)
            if version is None or catalog.archived(template_id):
                continue
            try:
                bundle = catalog.load(template_id, version)
            except Exception as exc:  # a broken template is left out, never offered
                log.warning("template.unavailable id=%s error=%s", template_id, type(exc).__name__)
                continue
            tests = read_tests(catalog.version_dir(template_id, version) / TESTS_DIR)
            out.append(
                {
                    "id": template_id,
                    "version": version,
                    "name": bundle.name,
                    "description": bundle.description,
                    "l0_policies": [p.name for p in bundle.l0.policies],
                    "decision_policies": [p.name for p in bundle.l1b.policies]
                    if bundle.l1b
                    else [],
                    "cases": sum(len(f.get("cases") or []) for f in tests),
                }
            )
        return out

    # ----------------------------------------------------------- create and archive

    async def _free_id(self, tenant: TenantInfo, repository: GuardrailRepository, base: str) -> str:
        for n in range(1, 100):
            candidate = base if n == 1 else f"{base[:60]}-{n}"
            taken = repository.versions(candidate) or await self.drafts.get(tenant, candidate)
            if not taken:
                return candidate
        raise ServiceError(409, f"no free id for {base}", code="guardrail_exists")

    async def create_guardrail(
        self,
        tenant: TenantInfo,
        *,
        name: str,
        description: str | None,
        bundle_id: str | None,
        source: dict,
        limit: int | None,
    ) -> dict:
        """A new guardrail as a draft without a base version, from a template or a copy of one
        of the tenant's guardrails, with its golden cases (ADR-0056 §2). Nothing is published."""

        repository = self.guardrails.for_tenant(tenant.slug)
        if "template" in source:
            origin_id = source["template"]
            origin = self.guardrails.for_tenant(self.catalog_tenant)
            version = origin.active(origin_id) if origin_id in self.templates else None
            if version is None or origin.archived(origin_id):
                raise ServiceError(404, f"no template {origin_id}", code="unknown_template")
            created_from = {"template": origin_id, "version": version}
        else:
            origin_id, origin = source["guardrail"], repository
            version = source.get("version") or origin.active(origin_id)
            if origin_id not in self._own_ids(tenant, repository) or version not in origin.versions(
                origin_id
            ):
                raise ServiceError(404, f"no guardrail {origin_id} here", code="unknown_guardrail")
            created_from = {"guardrail": origin_id, "version": version}
        if bundle_id is not None and (
            repository.versions(bundle_id) or await self.drafts.get(tenant, bundle_id)
        ):
            raise ServiceError(409, f"guardrail {bundle_id} exists", code="guardrail_exists")
        if limit is not None:
            count = await self.count(tenant)
            if count >= limit:
                raise ServiceError(
                    409,
                    f"the plan allows {limit} guardrails",
                    {"limit": limit, "count": count},
                    code="guardrail_limit_reached",
                )
        if bundle_id is None:
            bundle_id = await self._free_id(tenant, repository, slug_of(name))
        try:
            bundle = origin.load(origin_id, version)
        except Exception as exc:
            raise ServiceError(404, str(exc), code="unknown_guardrail") from exc
        data = to_policy_form(bundle_to_data(bundle))
        data.update(id=bundle_id, version=1, name=name)
        if description is not None:
            data["description"] = description
        tests = read_tests(origin.version_dir(origin_id, version) / TESTS_DIR)
        try:
            draft = await self.drafts.create(
                tenant, bundle_id, None, data, tests=tests, created_from=created_from
            )
        except Conflict as exc:
            raise ServiceError(409, str(exc), code="guardrail_exists") from exc
        return self.draft_view(draft)

    async def archive(
        self,
        tenant: TenantInfo,
        bundle_id: str,
        *,
        applications: list[str],
        note: str | None,
        actor: dict,
    ) -> dict:
        """Stop serving a guardrail and hide it (ADR-0056 §2); refused while an application
        uses it or a draft is open. ``applications``: the tenant's applications naming it."""

        repository = self.guardrails.for_tenant(tenant.slug)
        if bundle_id not in self._own_ids(tenant, repository) or repository.archived(bundle_id):
            raise ServiceError(404, f"no guardrail {bundle_id} here", code="unknown_guardrail")
        if await self.drafts.get(tenant, bundle_id) is not None:
            raise ServiceError(409, f"guardrail {bundle_id} has an open draft", code="has_draft")
        if applications:
            raise ServiceError(
                409,
                f"guardrail {bundle_id} protects applications",
                {"applications": sorted(applications)},
                code="guardrail_in_use",
            )
        previous = repository.archive(bundle_id, note, actor)
        return {"id": bundle_id, "archived": True, "previous_active": previous}

    # ----------------------------------------------------------- drafts

    def draft_view(self, draft: DraftInfo, tests: list[dict] | None = None) -> dict:
        _, problems = problems_of(draft.bundle)
        return {
            "bundle_id": draft.bundle_id,
            "base_version": draft.base_version,
            "revision": draft.revision,
            "sha256": draft_sha256(draft.bundle),
            "bundle": draft.bundle,
            "tests": draft.tests if draft.tests is not None else (tests or []),
            "problems": problems,
            "created_from": draft.created_from,
            "created_at": draft.created_at,
            "updated_at": draft.updated_at,
        }

    async def _draft_view(self, tenant: TenantInfo, draft: DraftInfo) -> dict:
        """With a draft older than its cases (``tests`` None): the base version's cases."""

        tests = None
        if draft.tests is None and draft.base_version is not None:
            repository = self.guardrails.for_tenant(tenant.slug)
            tests = read_tests(
                repository.version_dir(draft.bundle_id, draft.base_version) / TESTS_DIR
            )
        return self.draft_view(draft, tests)

    async def get_draft(self, tenant: TenantInfo, bundle_id: str) -> dict:
        draft = await self.drafts.get(tenant, bundle_id)
        if draft is None:
            raise ServiceError(404, f"guardrail {bundle_id} has no draft")
        return await self._draft_view(tenant, draft)

    async def create_draft(
        self, tenant: TenantInfo, bundle_id: str, base_version: int | None
    ) -> dict:
        if await self.drafts.get(tenant, bundle_id) is not None:
            raise ServiceError(409, f"guardrail {bundle_id} already has a draft")
        repository = self.guardrails.for_tenant(tenant.slug)
        if repository.archived(bundle_id):
            raise ServiceError(409, f"guardrail {bundle_id} is archived", code="archived")
        version = base_version or repository.active(bundle_id)
        if version is None:
            raise ServiceError(404, f"guardrail {bundle_id} has no active version")
        try:
            bundle = repository.load(bundle_id, version)
        except Exception as exc:
            raise ServiceError(404, str(exc)) from exc
        data = to_policy_form(bundle_to_data(bundle))
        try:
            # The cases stay with the base version until the case editor writes them (E5)
            draft = await self.drafts.create(tenant, bundle_id, version, data)
        except Conflict as exc:
            raise ServiceError(409, str(exc)) from exc
        return await self._draft_view(tenant, draft)

    async def save_draft(
        self, tenant: TenantInfo, bundle_id: str, data: dict, revision: int | None = None
    ) -> dict:
        if not isinstance(data, dict) or data.get("id") != bundle_id:
            raise ServiceError(400, "the draft must be a bundle object with the guardrail's id")
        try:  # saved even when invalid
            draft = await self.drafts.save(tenant, bundle_id, data, revision=revision)
        except StaleRevision as exc:
            raise ServiceError(
                412,
                "the draft was changed since it was read",
                {"revision": exc.current},
                code="draft_changed",
            ) from exc
        if draft is None:
            raise ServiceError(404, f"guardrail {bundle_id} has no draft")
        return await self._draft_view(tenant, draft)

    async def discard_draft(self, tenant: TenantInfo, bundle_id: str) -> None:
        if not await self.drafts.delete(tenant, bundle_id):
            raise ServiceError(404, f"guardrail {bundle_id} has no draft")

    # ----------------------------------------------------------- test and publish

    async def _valid_draft(
        self, tenant: TenantInfo, bundle_id: str
    ) -> tuple[DraftInfo, PolicyBundle]:
        draft = await self.drafts.get(tenant, bundle_id)
        if draft is None:
            raise ServiceError(404, f"guardrail {bundle_id} has no draft")
        bundle, problems = problems_of(draft.bundle)
        if bundle is None or problems:
            raise ServiceError(422, "the draft is not valid", {"problems": problems})
        return draft, bundle

    @staticmethod
    def _test_version(draft: DraftInfo) -> int:
        return draft.base_version or 1  # a never-published draft is tested as its version 1

    async def _run_tests(
        self,
        repository: GuardrailRepository,
        draft: DraftInfo,
        bundle: PolicyBundle,
        workdir: Path,
    ) -> RunReport:
        version = self._test_version(draft)
        version_dir = workdir / bundle.id / str(version)
        version_dir.mkdir(parents=True)
        (version_dir / BUNDLE_FILE).write_bytes(
            bundle_to_yaml(bundle.model_copy(update={"version": version}), "")
        )
        if draft.tests is not None:
            write_tests(draft.tests, version_dir / TESTS_DIR)
        else:
            base_tests = repository.version_dir(bundle.id, version) / TESTS_DIR
            if base_tests.is_dir():
                shutil.copytree(base_tests, version_dir / TESTS_DIR)
        return await run_golden(
            version_dir,
            detector=self.detector,
            default_tenant=repository.owner or self.guardrails.default_tenant,
        )

    async def test_draft(self, tenant: TenantInfo, bundle_id: str) -> dict:
        draft, bundle = await self._valid_draft(tenant, bundle_id)
        repository = self.guardrails.for_tenant(tenant.slug)
        with tempfile.TemporaryDirectory(prefix="umai-draft-") as tmp:
            report = await self._run_tests(repository, draft, bundle, Path(tmp))
        return report_to_data(report)

    async def publish(
        self,
        tenant: TenantInfo,
        bundle_id: str,
        *,
        activate: bool,
        accept: list[str],
        note: str | None,
        actor: dict,
    ) -> dict:
        draft, bundle = await self._valid_draft(tenant, bundle_id)
        repository = self.guardrails.for_tenant(tenant.slug)
        with tempfile.TemporaryDirectory(prefix="umai-draft-") as tmp:
            report = await self._run_tests(repository, draft, bundle, Path(tmp))
            failing = {c.case_id: c for c in report.cases if not c.passed}
            unaccepted = sorted(set(failing) - set(accept))
            if report.errors or unaccepted:
                raise ServiceError(
                    422,
                    "golden tests fail; fix the draft or accept the new behaviour of these cases",
                    {"unaccepted": unaccepted, "report": report_to_data(report)},
                )
            tests_dir = Path(tmp) / bundle_id / str(self._test_version(draft)) / TESTS_DIR
            accepted = {
                cid: (c.file, c.actual) for cid, c in failing.items() if c.actual is not None
            }
            if accepted:
                accept_cases(tests_dir, accepted)
            version = repository.publish(
                bundle,
                base_version=draft.base_version,
                tests_dir=tests_dir,
                note=note,
                signing_key=self.signing_key,
                actor=actor,
                created_from=draft.created_from,
            )
        previous = repository.activate(bundle_id, version, note, actor) if activate else None
        await self.drafts.delete(tenant, bundle_id)
        return {
            "version": version,
            "activated": activate,
            "previous_active": previous,
            "accepted": sorted(accepted),
            "tests": {"total": len(report.cases)},
        }

    def install_starter(self, tenant: TenantInfo, template_id: str, actor: dict) -> dict | None:
        """Copy the default tenant's active ``template_id`` into ``tenant`` as its version 1,
        signed for the new owner, and activate it (ADR-0033 §7). None when there is no template."""

        source = self.guardrails.for_tenant(self.guardrails.default_tenant)
        active = source.active(template_id)
        if active is None:
            return None
        target = self.guardrails.for_tenant(tenant.slug)
        if target.versions(template_id):
            return {"id": template_id, "version": target.active(template_id)}
        tests = source.version_dir(template_id, active) / TESTS_DIR
        version = target.publish(
            source.load(template_id, active),
            base_version=active,
            tests_dir=tests if tests.is_dir() else None,
            note="starter guardrail",
            signing_key=self.signing_key,
            actor=actor,
        )
        target.activate(template_id, version, "starter guardrail", actor)
        return {"id": template_id, "version": version}

    def activate(
        self, tenant: TenantInfo, bundle_id: str, version: int, note: str | None, actor: dict
    ) -> dict:
        repository = self.guardrails.for_tenant(tenant.slug)
        if repository.archived(bundle_id):
            raise ServiceError(409, f"guardrail {bundle_id} is archived", code="archived")
        try:
            previous = repository.activate(bundle_id, version, note, actor)
        except Exception as exc:
            raise ServiceError(422, str(exc)) from exc
        return {"active_version": version, "previous_active": previous}
