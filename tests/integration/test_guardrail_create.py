"""Creating guardrails in the console (ADR-0056 §2–§3, guardrails 0.1): templates, copies,
drafts without a base version, the plan limit, archive and draft revisions. Synthetic data."""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from umai_decision_engine.control_plane.app import create_app
from umai_decision_engine.control_plane.service import slug_of
from umai_decision_engine.policy.sources import DirectorySource
from umai_decision_engine.policy.store import BundleStore
from umai_decision_engine.settings import Settings

REPO_POLICIES = Path(__file__).resolve().parents[2] / "policies"
TEMPLATES = ["tpl-kvkk-data-protection", "tpl-prompt-attack", "tpl-blank"]

CASES = """
contract_version: "0.2"
cases:
  - id: tckn-blocked
    input: { text: "TC kimlik 10000000146" }
    expect: { action: BLOCK, entities: [TR_TCKN] }
"""

PLANS = """
contract_version: "0.3"
plans:
  community: { monthly_requests: null, max_guardrails: 2 }
"""

ACME = {"X-Umai-Tenant": "acme"}


@pytest.fixture
def client(write_bundle, detector, tmp_path):
    directory = write_bundle(bundle_yaml(), active=1)
    (directory / "tests").mkdir()
    (directory / "tests" / "cases.yaml").write_text(textwrap.dedent(CASES), encoding="utf-8")
    for template in TEMPLATES:  # the catalog is the default tenant
        shutil.copytree(REPO_POLICIES / template, write_bundle.root / template)
    plans = tmp_path / "plans.yaml"
    plans.write_text(textwrap.dedent(PLANS), encoding="utf-8")
    settings = Settings(
        env="development",
        policy_dir=write_bundle.root,
        control_db_path=tmp_path / "control.sqlite",
        applications_dir=tmp_path / "applications",
        plans_file=plans,
    )
    with TestClient(create_app(settings, detector=detector)) as c:
        assert c.post("/v1/tenants", json={"id": "acme"}).status_code == 201
        c.root = write_bundle.root
        yield c


def _create(client: TestClient, name: str, source: dict, headers=ACME, **extra):
    return client.post(
        "/v1/guardrails", json={"name": name, "from": source, **extra}, headers=headers
    )


def test_slugs_are_ascii_and_never_empty() -> None:
    assert slug_of("İK asistanı koruması") == "gr-ik-asistani-korumasi"
    assert slug_of("Şirket Çağrı Merkezi — Öğrenci") == "gr-sirket-cagri-merkezi-ogrenci"
    assert slug_of("!!!") == "gr-guardrail"


def test_templates_are_listed_in_order_and_kept_out_of_the_catalog_tenants_guardrails(
    client,
) -> None:
    templates = client.get("/v1/guardrail-templates").json()["templates"]
    # tpl-malicious-use is configured but has no bundle: not offered
    assert [t["id"] for t in templates] == TEMPLATES
    kvkk, attack, blank = templates
    assert kvkk["version"] == 1 and kvkk["decision_policies"] == []
    assert "Kimlik verileri" in kvkk["l0_policies"] and kvkk["cases"] > 20
    assert attack["decision_policies"] == ["Prompt saldırıları"]
    assert blank["cases"] == 1
    listed = [g["id"] for g in client.get("/v1/guardrails").json()["guardrails"]]
    assert listed == ["gr-test"]


def test_create_from_a_template_then_publish_version_1(client) -> None:
    created = _create(client, "İK asistanı koruması", {"template": "tpl-kvkk-data-protection"})
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["bundle_id"] == "gr-ik-asistani-korumasi"
    assert (draft["base_version"], draft["revision"], draft["problems"]) == (None, 1, [])
    assert draft["created_from"] == {"template": "tpl-kvkk-data-protection", "version": 1}
    assert draft["bundle"]["name"] == "İK asistanı koruması"
    assert {f["file"] for f in draft["tests"]} >= {"tests/l0-gate.yaml", "tests/policies.yaml"}
    assert len(draft["sha256"]) == 64

    (listed,) = client.get("/v1/guardrails", headers=ACME).json()["guardrails"]
    assert (listed["id"], listed["active_version"], listed["has_draft"]) == (
        "gr-ik-asistani-korumasi",
        None,
        True,
    )
    assert listed["created_from"]["template"] == "tpl-kvkk-data-protection"
    summary = client.get("/v1/guardrails/gr-ik-asistani-korumasi", headers=ACME).json()
    assert summary["versions"] == [] and summary["draft"]["base_version"] is None

    tested = client.post("/v1/guardrails/gr-ik-asistani-korumasi/draft/test", headers=ACME)
    assert tested.json()["passed"] and tested.json()["total"] > 20
    published = client.post(
        "/v1/guardrails/gr-ik-asistani-korumasi/draft/publish",
        json={"activate": True},
        headers=ACME,
    )
    assert published.status_code == 200, published.text
    assert published.json()["version"] == 1
    summary = client.get("/v1/guardrails/gr-ik-asistani-korumasi", headers=ACME).json()
    assert summary["active_version"] == 1 and summary["draft"] is None
    assert [e["action"] for e in summary["history"]] == ["create", "publish", "activate"]
    assert summary["created_from"] == {"template": "tpl-kvkk-data-protection", "version": 1}
    version_dir = client.root / "tenants" / "acme" / "gr-ik-asistani-korumasi" / "1"
    assert (version_dir / "tests" / "l0-gate.yaml").is_file()
    assert "from a new guardrail" in (version_dir / "bundle.yaml").read_text(encoding="utf-8")


def test_create_as_a_copy_and_the_refusals(client) -> None:
    created = _create(client, "Kopya", {"guardrail": "gr-test"}, headers={})
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["bundle_id"] == "gr-kopya"
    assert draft["created_from"] == {"guardrail": "gr-test", "version": 1}
    assert [f["file"] for f in draft["tests"]] == ["tests/cases.yaml"]
    # a second one with the same name gets a number
    assert _create(client, "Aynı", {"template": "tpl-blank"}).json()["bundle_id"] == "gr-ayni"
    assert _create(client, "Aynı", {"template": "tpl-blank"}).json()["bundle_id"] == "gr-ayni-2"

    def error(response) -> tuple[int, str]:
        return response.status_code, response.json()["detail"]["error"]

    taken = _create(client, "X", {"guardrail": "gr-test"}, headers={}, id="gr-test")
    assert error(taken) == (409, "guardrail_exists")
    assert error(_create(client, "X", {"template": "tpl-nope"})) == (404, "unknown_template")
    # another tenant's guardrail cannot be copied
    assert error(_create(client, "X", {"guardrail": "gr-test"})) == (404, "unknown_guardrail")
    both = _create(client, "X", {"template": "tpl-blank", "guardrail": "gr-test"})
    assert both.status_code == 422


def test_the_plan_limits_guardrails_and_a_discarded_draft_frees_its_place(client) -> None:
    assert _create(client, "Bir", {"template": "tpl-blank"}).status_code == 201
    assert _create(client, "İki", {"template": "tpl-blank"}).status_code == 201
    refused = _create(client, "Üç", {"template": "tpl-blank"})
    assert refused.status_code == 409
    assert refused.json()["detail"] | {"detail": None} == {
        "error": "guardrail_limit_reached",
        "detail": None,
        "limit": 2,
        "count": 2,
    }
    entitlements = client.get("/v1/entitlements", headers=ACME).json()
    assert entitlements["limits"]["max_guardrails"] == 2
    assert entitlements["used"]["guardrails"] == 2
    assert client.delete("/v1/guardrails/gr-iki/draft", headers=ACME).status_code == 204
    assert client.get("/v1/guardrails/gr-iki", headers=ACME).status_code == 404
    assert _create(client, "Üç", {"template": "tpl-blank"}).status_code == 201


def test_archive_stops_serving_and_hides_the_guardrail(client) -> None:
    app = client.put("/v1/applications/hr-assistant", json={"guardrail": "gr-test"})
    assert app.status_code == 201, app.text
    in_use = client.post("/v1/guardrails/gr-test/archive", json={})
    assert in_use.status_code == 409
    assert in_use.json()["detail"]["error"] == "guardrail_in_use"
    assert in_use.json()["detail"]["applications"] == ["hr-assistant"]
    assert client.delete("/v1/applications/hr-assistant").status_code == 200

    assert client.post("/v1/guardrails/gr-test/draft", json={}).status_code == 201
    assert client.post("/v1/guardrails/gr-test/archive", json={}).json()["detail"]["error"] == (
        "has_draft"
    )
    assert client.delete("/v1/guardrails/gr-test/draft").status_code == 204

    archived = client.post("/v1/guardrails/gr-test/archive", json={"note": "replaced"})
    assert archived.status_code == 200, archived.text
    assert archived.json() == {"id": "gr-test", "archived": True, "previous_active": 1}
    pointer = (client.root / "gr-test" / "active").read_text(encoding="utf-8")
    assert pointer.strip() == "archived"  # engines stop serving it (below)
    assert client.get("/v1/guardrails").json()["guardrails"] == []
    assert client.get("/v1/guardrails/gr-test").json()["archived"] is True
    again = client.post("/v1/guardrails/gr-test/activate", json={"version": 1})
    assert again.json()["detail"]["error"] == "archived"
    draft = client.post("/v1/guardrails/gr-test/draft", json={"base_version": 1})
    assert draft.json()["detail"]["error"] == "archived"
    bind = client.put("/v1/applications/hr-assistant", json={"guardrail": "gr-test"})
    assert bind.status_code == 422
    assert client.post("/v1/guardrails/gr-test/archive", json={}).status_code == 404
    log = (client.root / "gr-test" / "changelog.jsonl").read_text(encoding="utf-8")
    assert json.loads(log.splitlines()[-1])["action"] == "archive"


def test_a_stale_revision_is_refused(client) -> None:
    draft = _create(client, "Rev", {"template": "tpl-blank"}).json()
    bundle = draft["bundle"]
    path = "/v1/guardrails/gr-rev/draft"
    saved = client.put(path, json={"bundle": bundle}, headers={**ACME, "If-Match": '"1"'})
    assert saved.json()["revision"] == 2
    stale = client.put(path, json={"bundle": bundle}, headers={**ACME, "If-Match": '"1"'})
    assert stale.status_code == 412
    assert stale.json()["detail"] | {"detail": None} == {
        "error": "draft_changed",
        "detail": None,
        "revision": 2,
    }
    assert client.put(path, json={"bundle": bundle}, headers=ACME).json()["revision"] == 3
    bad = client.put(path, json={"bundle": bundle}, headers={**ACME, "If-Match": "x"})
    assert bad.status_code == 400


def test_creating_is_audited_with_its_origin(client) -> None:
    _create(client, "Denetim", {"template": "tpl-prompt-attack"})
    events = client.get("/v1/audit-events", headers=ACME).json()["events"]
    created = next(e for e in events if e["action"] == "guardrail.create")
    assert created["target"] == {"type": "guardrail", "id": "gr-denetim"}
    assert created["details"] == {
        "name": "Denetim",
        "from": {"template": "tpl-prompt-attack", "version": 1},
    }


def test_an_engine_stops_serving_an_archived_guardrail(client) -> None:
    """A missing pointer keeps the last version (a fault); an archived one keeps none."""

    store = BundleStore(DirectorySource(client.root), public_keys={}, require_signed=False)
    store.reload()
    assert store.get("gr-test") is not None
    assert client.post("/v1/guardrails/gr-test/archive", json={}).status_code == 200
    store.reload()
    assert store.get("gr-test") is None
    assert store.get("gr-test", 1) is not None  # its versions stay loaded
    assert not [e for e in store._state.errors if "gr-test" in e.location]
