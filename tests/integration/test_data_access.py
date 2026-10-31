"""Data access for organizations (ADR-0047, tagged-action 0.6, data-export 0.1): find a decision by
any of its ids, filter by entity type and policy, the decisions and usage CSVs (columns, escaping,
Turkish text, caps, roles) and the organization archive (owners only, contents, nothing secret,
one tenant, limits). SQLite; the JSON filters on Postgres are in tests/db. Synthetic data only."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from tests.integration.test_policy_service_authz import (  # noqa: F401
    _as,
    _assertion,
    client,
    gateway_client,
)
from tests.integration.test_records_api import T0, event, store, usage  # noqa: F401
from umai_decision_engine.contracts.events import DecisionEvent, L1bOutcomeRecord
from umai_decision_engine.contracts.exports import (
    DECISIONS_CSV_COLUMNS,
    USAGE_CSV_COLUMNS,
    ArchiveManifest,
    UsageLine,
)
from umai_decision_engine.db.models import BillingCheckoutRow
from umai_decision_engine.db.repositories import TenantRepository
from umai_decision_engine.db.session import make_engine, sqlite_url, tenant_session

BLOCKED = {"action": "BLOCK", "severity": "HIGH", "decided_by": "L0"}
MASKED = {"action": "ALLOW_WITH_MODIFICATIONS", "severity": "LOW", "decided_by": "L0"}


def _seed(store) -> None:  # noqa: F811
    store(
        [
            event(
                1,
                request_id="req-Satış-1",
                decision=BLOCKED,
                entities=[{"type": "TR_TCKN", "count": 2, "distinct": 1}],
                matched=[
                    {"kind": "l0_policy", "id": "identity-data", "version": 1, "action": "BLOCK"}
                ],
            ),
            event(
                2,
                source="gateway",
                gateway_call_id="gc-0123456789abcdef0123456789abcdef",
                decision=MASKED,
                entities=[{"type": "EMAIL_ADDRESS", "count": 1, "distinct": 1}],
                matched=[
                    {
                        "kind": "l0_policy",
                        "id": "contact-data",
                        "version": 1,
                        "action": "ALLOW_WITH_MODIFICATIONS",
                    }
                ],
            ),
            event(
                3,
                source="gateway",
                gateway_call_id="gc-0123456789abcdef0123456789abcdef",  # the same call's answer
                phase="POST_LLM",
            ),
            event(4, request_id="req-other"),
            event(5, tenant="other", request_id="req-Satış-1"),  # another tenant, same id
        ]
    )


def _ids(client, **params) -> list[int]:  # noqa: F811
    page = client.get("/v1/decision-events", params=params)
    assert page.status_code == 200, page.text
    return sorted(int(e["event_id"][3:], 16) for e in page.json()["events"])


# ------------------------------------------------------------------ search and filters


def test_search_finds_a_decision_by_each_of_its_ids(client, store) -> None:  # noqa: F811
    _seed(store)
    assert _ids(client, q=f"ta-{4:032x}") == [4]  # event id
    assert _ids(client, q="req-Satış-1") == [1]  # request id; the other tenant's is not found
    assert _ids(client, q="gc-0123456789abcdef0123456789abcdef") == [2, 3]  # every check
    assert _ids(client, q=f"  ta-{4:032x} ") == [4]  # pasted with spaces
    assert _ids(client, q="req-nothing") == []
    assert _ids(client, q="req-Satış-1", action="ALLOW") == []  # with the other filters
    assert client.get("/v1/decision-events", params={"q": "x" * 129}).status_code == 422


def test_decisions_filter_by_entity_type_and_policy(client, store) -> None:  # noqa: F811
    _seed(store)
    assert _ids(client, entity_type="TR_TCKN") == [1]
    assert _ids(client, entity_type="EMAIL_ADDRESS", source="gateway") == [2]
    assert _ids(client, entity_type="IBAN_CODE") == []
    assert _ids(client, policy_id="contact-data") == [2]
    assert _ids(client, policy_id="identity-data", entity_type="EMAIL_ADDRESS") == []


def test_the_summary_follows_the_new_filters(client, store) -> None:  # noqa: F811
    _seed(store)
    window = {"since": "2026-10-29T00:00:00Z", "until": "2026-11-01T00:00:00Z"}
    found = client.get(
        "/v1/decision-events/summary", params={**window, "entity_type": "TR_TCKN"}
    ).json()
    assert (found["total"], found["by_action"]) == (1, {"BLOCK": 1})
    call = client.get(
        "/v1/decision-events/summary",
        params={**window, "q": "gc-0123456789abcdef0123456789abcdef"},
    ).json()
    assert call["total"] == 2 and call["by_source"] == {"gateway": 2}
    policy = client.get(
        "/v1/decision-events/summary", params={**window, "policy_id": "contact-data"}
    ).json()
    assert policy["top_policies"] == [{"kind": "l0_policy", "id": "contact-data", "decisions": 1}]


# ------------------------------------------------------------------ decisions CSV


def _csv(response) -> list[dict[str, str]]:
    assert response.headers["content-type"].startswith("text/csv")
    assert response.content.startswith("﻿".encode())  # BOM: Excel reads UTF-8
    text = response.content.decode("utf-8-sig")
    assert "\r\n" in text
    reader = csv.DictReader(io.StringIO(text, newline=""))
    rows = list(reader)
    return reader.fieldnames, rows


def test_the_decisions_csv_has_the_contract_columns_and_the_lists_filters(client, store) -> None:  # noqa: F811
    _seed(store)
    response = client.get("/v1/decision-events/export.csv", params={"source": "gateway"})
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="umai-decisions-default-'
    )
    columns, rows = _csv(response)
    assert tuple(columns) == DECISIONS_CSV_COLUMNS
    assert [r["event_id"] for r in rows] == [f"ta-{3:032x}", f"ta-{2:032x}"]  # newest first
    masked = rows[1]
    assert masked["action"] == "ALLOW_WITH_MODIFICATIONS"
    assert masked["entities"] == "EMAIL_ADDRESS:1"
    assert masked["policies"] == "l0_policy:contact-data"
    assert masked["occurred_at"] == "2026-10-30T08:02:00Z"
    assert masked["gateway_call_id"] == "gc-0123456789abcdef0123456789abcdef"
    assert response.headers["x-umai-export-rows"] == "2"


def test_the_decisions_csv_escapes_and_keeps_turkish_text(client, store) -> None:  # noqa: F811
    store(
        [
            event(
                1,
                request_id='=HYPERLINK("http://x.example","tıkla")',  # a formula from a caller
                subject={"role": "satış, müdür", "department": 'Çağrı "Merkezi"\nİzmir'},
                destination={"app": "chatgpt.com", "class": "external_llm"},
                errors=[{"layer": "L1b", "type": "L1B_TIMEOUT", "retryable": True}],
                latency_ms={"total": 301.25, "l0": 1.0, "l1b": 300.0},
            )
        ]
    )
    _, (row,) = _csv(client.get("/v1/decision-events/export.csv"))
    assert row["request_id"] == '\'=HYPERLINK("http://x.example","tıkla")'  # inert
    assert row["subject_role"] == "satış, müdür"  # quoted, not split
    assert row["subject_department"] == 'Çağrı "Merkezi"\nİzmir'
    assert row["destination_class"] == "external_llm"
    assert row["errors"] == "L1b:L1B_TIMEOUT"
    assert (row["latency_ms"], row["latency_l0_ms"], row["latency_l1b_ms"]) == (
        "301.25",
        "1",
        "300",
    )
    assert (row["review"], row["degraded"], row["floor"]) == ("false", "false", "")


def test_a_decisions_csv_above_the_cap_is_refused_before_anything_is_sent(client, store) -> None:  # noqa: F811
    _seed(store)
    client.app.state.export_limits.decisions_csv = 3  # 100 000 in service
    response = client.get("/v1/decision-events/export.csv")
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "export_too_large"
    assert response.json()["detail"]["limit"] == 3
    narrower = client.get("/v1/decision-events/export.csv", params={"phase": "PRE_LLM"})
    assert narrower.status_code == 200  # 3 of the 4 events


# ------------------------------------------------------------------ usage CSV


def test_the_usage_csv_has_one_line_per_day_and_dimension(client, store) -> None:  # noqa: F811
    store(
        [
            usage(1, "evaluations", 1, at=T0),
            usage(2, "evaluations", 1, at=T0 + timedelta(hours=1)),
            usage(
                3,
                "provider_input_tokens",
                812,
                at=T0,
                source="gateway",
                provider="openai",
                model="gpt-4.1-mini",
            ),
            usage(4, "evaluations", 1, at=T0 + timedelta(days=1), environment="test"),
            usage(5, "evaluations", 1, at=datetime(2026, 9, 30, 23, 59, tzinfo=UTC)),  # September
        ]
    )
    response = client.get("/v1/usage/export.csv", params={"month": "2026-10"})
    assert response.status_code == 200, response.text
    assert 'filename="umai-usage-default-2026-10.csv"' in response.headers["content-disposition"]
    columns, rows = _csv(response)
    assert tuple(columns) == USAGE_CSV_COLUMNS
    assert [
        (r["date"], r["meter"], r["quantity"], r["records"], r["environment"]) for r in rows
    ] == [
        ("2026-10-30", "evaluations", "2", "2", "live"),
        ("2026-10-30", "provider_input_tokens", "812", "1", "live"),
        ("2026-10-31", "evaluations", "1", "1", "test"),
    ]
    assert rows[1]["model"] == "gpt-4.1-mini" and rows[0]["model"] == ""
    for row in rows:
        UsageLine.model_validate({k: (v or None) for k, v in row.items()})
    assert client.get("/v1/usage/export.csv", params={"month": "2026-13"}).status_code == 422
    client.app.state.export_limits.usage_csv = 2
    assert client.get("/v1/usage/export.csv", params={"month": "2026-10"}).status_code == 422


def test_csv_exports_need_the_read_permissions(client) -> None:  # noqa: F811
    billing = _as(_assertion(roles=("billing",), sub="auth0|bill"))
    assert client.get("/v1/usage/export.csv", headers=billing).status_code == 200
    assert client.get("/v1/decision-events/export.csv", headers=billing).status_code == 403
    viewer = _as(_assertion(roles=("viewer",), sub="auth0|vic"))
    assert client.get("/v1/decision-events/export.csv", headers=viewer).status_code == 200


# ------------------------------------------------------------------ organization archive


def _archive(response) -> dict[str, bytes]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="umai-default-export-'
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _lines(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines()]


def _checkout_token(tmp_path, token: str) -> None:
    async def go() -> None:
        engine = make_engine(sqlite_url(tmp_path / "control.sqlite"))
        tenant = await TenantRepository(engine).get("default")
        async with tenant_session(engine, tenant.id) as session:
            session.add(
                BillingCheckoutRow(
                    tenant_id=tenant.id,
                    checkout_id="co-1",
                    token=token,
                    edition="pro",
                    pricing_plan_reference="plan-ref-1",
                    status="open",
                    created_by="person:auth0|olga",
                    expires_at=datetime.now(UTC) + timedelta(minutes=30),
                )
            )
        await engine.dispose()

    asyncio.run(go())


def test_an_owner_exports_the_organization_with_nothing_secret_in_it(
    gateway_client,  # noqa: F811
    store,  # noqa: F811
    tmp_path,
) -> None:
    api = gateway_client
    owner = _as(_assertion(roles=("owner",), sub="auth0|olga"))
    # Configuration, people, keys and billing through the API, as the console makes them.
    created = api.post("/v1/tenants/default/keys", json={"name": "sdk"}, headers=owner).json()
    key_secret = created["secret"]
    invited = api.post(
        "/v1/organizations/default/invitations",
        json={"email": "yeni.uye@example.com", "roles": ["viewer"]},
        headers=owner,
    ).json()
    invitation_token = invited["token"]
    provider = {"name": "Kurum OpenAI", "kind": "openai", "credential": "pass_through"}
    assert api.put("/v1/providers/kurum", json=provider, headers=owner).status_code == 201
    application = {"name": "Destek Asistanı", "guardrail": "gr-test"}
    assert api.put("/v1/applications/destek", json=application, headers=owner).status_code == 201
    account = {
        "company_name": "Örnek Teknoloji A.Ş.",
        "tax_number": "1234567890",
        "tax_office": "Kadıköy",
        "contact_name": "Ayşe",
        "contact_surname": "Demir",
        "email": "fatura@example.com",
        "phone": "+905551112233",
        "address": "Örnek Mah. Deneme Sok. No: 1",
        "city": "İstanbul",
    }
    assert api.put("/v1/billing/account", json=account, headers=owner).status_code == 200
    checkout_token = f"synthetic-checkout-token-{uuid.uuid4().hex}"
    _checkout_token(tmp_path, checkout_token)
    outcome = L1bOutcomeRecord.model_validate(
        {
            "event_id": f"ta-{1:032x}",
            "tenant_id": "default",
            "occurred_at": T0,
            "bundle_id": "gr-test",
            "phase": "PRE_LLM",
            "policy_id": "prompt-attack",
            "policy_version": 1,
            "enforcement": "shadow",
            "outcome": "decided",
            "action": "ALLOW",
        }
    )
    store([event(1), event(2, tenant="other"), outcome, usage(1, "evaluations", 1, at=T0)])

    files = _archive(api.get("/v1/organizations/default/export.zip", headers=owner))

    manifest = ArchiveManifest.model_validate_json(files["manifest.json"])
    assert manifest.organization == "default"
    assert manifest.generated_by == {"type": "person", "sub": "auth0|olga"}
    assert {f.path for f in manifest.files} == set(files)
    assert manifest.retention_days == {"decision_events": 30, "l1b_outcomes": 30, "usage": 400}
    organization = json.loads(files["organization.json"])
    assert organization["id"] == "default" and "limits" in organization
    people = json.loads(files["members.json"])
    assert {m["user"]["email"]: m["roles"] for m in people["members"]}["olga@example.com"] == [
        "owner"
    ]
    assert [i["email"] for i in people["invitations"]] == ["yeni.uye@example.com"]
    (key,) = json.loads(files["api_keys.json"])["keys"]
    assert key["name"] == "sdk" and key["status"] == "active"
    gateway = json.loads(files["applications.json"])
    assert gateway["valid"] is True
    assert {p["provider_id"] for p in gateway["providers"]} == {"openai", "kurum"}
    assert [a["name"] for a in gateway["applications"]] == ["Destek Asistanı"]
    assert "guardrails/gr-test/1/bundle.yaml" in files
    index = json.loads(files["guardrails/index.json"])["guardrails"]
    assert index[0]["id"] == "gr-test" and index[0]["active_version"] == 1
    events = _lines(files["records/decision_events.jsonl"])
    assert [DecisionEvent.model_validate(e).event_id for e in events] == [f"ta-{1:032x}"]
    assert _lines(files["records/l1b_outcomes.jsonl"])[0]["policy_id"] == "prompt-attack"
    assert _lines(files["records/usage_daily.jsonl"])[0]["meter"] == "evaluations"
    billing = json.loads(files["billing.json"])
    assert billing["account"]["city"] == "İstanbul"
    assert [e["kind"] for e in billing["events"]] == ["account_saved"]

    everything = b"".join(files.values())
    for secret in (key_secret, invitation_token, checkout_token):
        assert secret.encode() not in everything
    for field in (b"secret_sha256", b"token_sha256", b'"token"'):
        assert field not in everything
    assert f"ta-{2:032x}".encode() not in everything  # another tenant's record


def test_only_an_owner_may_export_the_organization(client) -> None:  # noqa: F811
    for roles in (("admin",), ("viewer",), ("billing",), ("policy_editor", "approver")):
        person = _as(_assertion(roles=roles, sub=f"auth0|{roles[0]}"))
        response = client.get("/v1/organizations/default/export.zip", headers=person)
        assert response.status_code == 403, roles
    assert client.get("/v1/organizations/default/export.zip").status_code == 403  # token alone
    elsewhere = _as(_assertion(tenant="other-org", roles=("owner",), sub="auth0|own"))
    assert client.get("/v1/organizations/default/export.zip", headers=elsewhere).status_code in (
        403,
        404,
    )


def test_an_archive_above_the_limit_or_already_running_is_refused(client, store) -> None:  # noqa: F811
    _seed(store)
    owner = _as(_assertion(roles=("owner",), sub="auth0|olga"))
    exporter = client.app.state.exports
    exporter.max_records = 3  # 500 000 in service
    response = client.get("/v1/organizations/default/export.zip", headers=owner)
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error": "export_too_large",
        "detail": "more than 3 records match; export decisions per period as CSV and contact "
        "support",
        "limit": 3,
    }
    exporter.max_records = 100
    exporter._running.add("default")
    response = client.get("/v1/organizations/default/export.zip", headers=owner)
    assert response.status_code == 409 and response.json()["detail"]["error"] == "export_running"
    exporter._running.discard("default")
    assert client.get("/v1/organizations/default/export.zip", headers=owner).status_code == 200


@pytest.mark.parametrize("value", ["=1+2", "+1", "-1", "@SUM(A1)", "\tx", "plain", "", None])
def test_csv_cells_starting_like_a_formula_are_made_inert(value) -> None:
    from umai_decision_engine.control_plane.exports import cell

    out = cell(value)
    if value and value[0] in "=+-@\t":
        assert out == "'" + value
    else:
        assert out == (value or "")
