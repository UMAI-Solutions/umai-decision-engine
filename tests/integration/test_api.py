"""API end to end with a temporary policy directory (no network, no models)."""

from __future__ import annotations

import base64
import os

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.conftest import bundle_yaml
from umai_decision_engine.api.app import create_app
from umai_decision_engine.contracts.evaluation import ValidationErrorBody
from umai_decision_engine.settings import Settings

PII_TEXT = "TC kimlik 10000000146 ve IBAN TR33 0006 1005 1978 6457 8413 26"
CLEAN_TEXT = "Toplantı notlarını üç maddede özetler misin?"


def _request(text: str = PII_TEXT, **overrides):
    body = {
        "contract_version": "0.4",
        "request_id": "req-1",
        "timestamp": "2026-10-16T09:00:00Z",
        "tenant_id": "tenant-a",
        "bundle": {"id": "gr-test"},
        "phase": "PRE_LLM",
        "content": {
            "messages": [{"role": "user", "content": text}],
            "focus": "LAST_USER_MESSAGE",
        },
        "flags": {"trace": True},
    }
    body.update(overrides)
    return body


API_TOKEN = "test-api-token"  # synthetic


@pytest.fixture
def make_client(write_bundle, detector):
    clients = []

    def _make(*, own_detector: bool = False, **settings_overrides):
        env = settings_overrides.pop("env", "development")
        if env != "development":
            settings_overrides.setdefault("api_token", API_TOKEN)
        # The fixture's bundles sit directly under the policy root: the default tenant's.
        settings_overrides.setdefault("default_tenant_id", "tenant-a")
        settings = Settings(
            env=env,
            policy_dir=write_bundle.root,
            bundle_poll_interval_s=0,
            l0_threads=1,
            **settings_overrides,
        )
        token = settings.api_token
        headers = {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}
        # The shared detector uses a fixed test key provider; own_detector uses the settings'.
        client = TestClient(
            create_app(settings, detector=None if own_detector else detector), headers=headers
        )
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


def _touch(path) -> None:
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


# ------------------------------------------------------------------ basics


def test_health_and_ready(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    client = make_client()
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz")
    assert ready.status_code == 200 and ready.json()["bundles"] == 1


def test_clean_input_goes_to_l1(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    body = make_client().post("/v1/evaluate", json=_request(CLEAN_TEXT)).json()
    assert body["decision"]["action"] == "ALLOW" and body["decision"]["decided_by"] == "L1a"
    assert [t["layer"] for t in body["trace"]] == ["L0", "L1a"]
    assert body["tags"] == [] and body["entities"] == []


# ------------------------------------------------------------------ L0 gate (ADR-0014)


def test_l0_detection_blocks_and_l1_does_not_run(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    response = make_client().post("/v1/evaluate", json=_request())
    body = response.json()
    assert body["decision"] == {
        "action": "BLOCK",
        "allowed": False,
        "severity": "HIGH",
        "reason": "L0: TR_TCKN, TR_IBAN detected",
        "decided_by": "L0",
        "matched": [  # provenance (evaluation API 0.5): legacy bundles name on_detect.default
            {
                "kind": "l0_on_detect",
                "id": "on_detect.default",
                "action": "BLOCK",
                "entities": ["TR_TCKN", "TR_IBAN"],
            }
        ],
    }
    assert [t["layer"] for t in body["trace"]] == ["L0"]  # no L1 step at all
    assert "l1a" not in body["latency_ms"]
    assert {t["code"] for t in body["tags"]} == {"data.pii.tr_tckn", "data.pii.tr_iban"}
    assert body["bundle"]["version"] == 1 and len(body["bundle"]["sha256"]) == 64
    assert "10000000146" not in response.text and "TR33" not in response.text


RULE = (
    "[{id: r1, when: {all: [{field: destination.class, operator: EQUALS, value: external_llm}]},"
    " effect: BLOCK}]"
)


def test_l1_logic_is_never_reached_when_l0_detects(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(l1a_rules=RULE, failure="fail_open"), active=1)
    body = make_client().post("/v1/evaluate", json=_request()).json()
    assert body["decision"]["decided_by"] == "L0" and body["decision"]["action"] == "BLOCK"
    assert body["errors"] == [] and body["degraded"] is False


MASK_EMAIL = """
on_detect:
  actions:
    EMAIL_ADDRESS: ALLOW_WITH_MODIFICATIONS
"""


def test_mask_action_returns_redacted_text(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(l0_extra=MASK_EMAIL), active=1)
    response = make_client().post(
        "/v1/evaluate", json=_request("Bana ali@example.com adresinden yaz")
    )
    body = response.json()
    assert body["decision"]["action"] == "ALLOW_WITH_MODIFICATIONS"
    assert body["decision"]["allowed"] is True and body["decision"]["decided_by"] == "L0"
    assert body["output_modifications"] == {
        "redacted_text": "Bana <EMAIL_ADDRESS_1> adresinden yaz",
        "entities": ["EMAIL_ADDRESS"],
    }
    assert "ali@example.com" not in response.text


def test_strictest_type_wins(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(l0_extra=MASK_EMAIL), active=1)
    body = (
        make_client()
        .post("/v1/evaluate", json=_request("mail ali@example.com, TC 10000000146"))
        .json()
    )
    assert body["decision"]["action"] == "BLOCK"
    assert body["decision"]["reason"] == "L0: TR_TCKN detected"
    assert "output_modifications" not in body


KYC_EXCEPTION = """
exceptions:
  - id: ex-kyc-internal
    when:
      all:
        - { field: subject.department, operator: EQUALS, value: KYC }
        - { field: destination.class, operator: EQUALS, value: internal_app }
    entities: [TR_TCKN]
    action: ALLOW
"""


@pytest.mark.parametrize(
    "department, destination, text, expected, reason",
    [
        (
            "KYC",
            "internal_app",
            "TC 10000000146",
            "ALLOW",
            "L0: TR_TCKN allowed by exception ex-kyc-internal",
        ),
        (
            "kyc",
            "internal_app",
            "TC 10000000146",
            "ALLOW",
            "L0: TR_TCKN allowed by exception ex-kyc-internal",
        ),  # case-insensitive
        ("KYC", "external_llm", "TC 10000000146", "BLOCK", "L0: TR_TCKN detected"),
        ("SALES", "internal_app", "TC 10000000146", "BLOCK", "L0: TR_TCKN detected"),
        ("KYC", "internal_app", PII_TEXT, "BLOCK", "L0: TR_IBAN detected"),  # IBAN not covered
    ],
)
def test_deterministic_exception(
    write_bundle, make_client, department, destination, text, expected, reason
) -> None:
    write_bundle(bundle_yaml(l0_extra=KYC_EXCEPTION), active=1)
    request = _request(text, subject={"department": department}, destination={"class": destination})
    body = make_client().post("/v1/evaluate", json=request).json()
    assert body["decision"]["action"] == expected
    assert body["decision"]["reason"] == reason
    assert body["decision"]["decided_by"] == "L0"


def test_exception_needs_the_facts(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(l0_extra=KYC_EXCEPTION), active=1)
    body = make_client().post("/v1/evaluate", json=_request("TC 10000000146")).json()
    assert body["decision"]["action"] == "BLOCK"  # no subject / destination sent → no match


STEP_UP = """
on_detect:
  default: STEP_UP_APPROVAL
"""


@pytest.mark.parametrize(
    "phase, expected", [("PRE_LLM", "STEP_UP_APPROVAL"), ("TOOL_INPUT", "BLOCK")]
)
def test_step_up_without_ui_blocks(write_bundle, make_client, phase, expected) -> None:
    write_bundle(bundle_yaml(l0_extra=STEP_UP), active=1)
    body = make_client().post("/v1/evaluate", json=_request(phase=phase)).json()
    assert body["decision"]["action"] == expected


def test_monitor_mode_reports_would_have(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(mode="MONITOR", l0_extra=MASK_EMAIL), active=1)
    body = make_client().post("/v1/evaluate", json=_request("mail ali@example.com")).json()
    assert body["decision"]["action"] == "ALLOW" and body["decision"]["allowed"] is True
    assert body["would_have"]["action"] == "ALLOW_WITH_MODIFICATIONS"
    assert "output_modifications" not in body  # MONITOR never changes content


@pytest.mark.parametrize(
    "extra, message",
    [
        ("on_detect:\n  default: ALLOW", "default"),
        ("on_detect:\n  actions:\n    TR_TCKN: ALLOW_WITH_WARNINGS", "TR_TCKN"),
        ("on_detect:\n  actions:\n    TR_VKN: BLOCK", "TR_VKN"),
        (
            "exceptions:\n  - id: e1\n    when: {all: [{field: subject.role, operator: EQUALS,"
            " value: x}]}\n    entities: [TR_VKN]",
            "TR_VKN",
        ),
        (
            "exceptions:\n  - id: e1\n    when: {all: [{field: secret.thing, operator: EXISTS}]}",
            "secret.thing",
        ),
    ],
)
def test_invalid_gate_config_rejected(write_bundle, make_client, extra, message) -> None:
    write_bundle(bundle_yaml(l0_extra=extra), active=1)
    status = make_client().get("/v1/bundles").json()
    assert status["bundles"] == []
    assert any(message in e["error"] for e in status["errors"])


# ------------------------------------------------------------------ L1 (not implemented)


@pytest.mark.parametrize(
    "failure, phase, expected",
    [
        ("fail_closed", "PRE_LLM", "BLOCK"),
        ("fail_open", "PRE_LLM", "ALLOW"),
        ("step_up", "PRE_LLM", "STEP_UP_APPROVAL"),
        ("step_up", "TOOL_INPUT", "BLOCK"),  # no UI → behaves as fail_closed
    ],
)
def test_l1_logic_not_silently_allowed(write_bundle, make_client, failure, phase, expected) -> None:
    write_bundle(bundle_yaml(failure=failure, l1a_rules=RULE), active=1)
    body = make_client().post("/v1/evaluate", json=_request(CLEAN_TEXT, phase=phase)).json()
    assert body["decision"]["action"] == expected
    assert body["degraded"] is True
    assert body["errors"][0]["type"] == "L1_NOT_IMPLEMENTED"


# ------------------------------------------------------------------ configuration paths


def test_explicit_version_and_missing_bundle(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    client = make_client()
    ok = client.post(
        "/v1/evaluate", json=_request(CLEAN_TEXT, bundle={"id": "gr-test", "version": 1})
    )
    assert ok.json()["decision"]["action"] == "ALLOW"
    missing = client.post(
        "/v1/evaluate", json=_request(bundle={"id": "gr-test", "version": 9})
    ).json()
    assert missing["decision"]["action"] == "BLOCK"
    assert missing["errors"][0]["type"] == "CONFIG_MISSING" and missing["degraded"]


def test_phase_not_enabled_blocks(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    body = make_client().post("/v1/evaluate", json=_request(phase="MEMORY_WRITE")).json()
    assert body["decision"]["action"] == "BLOCK" and body["errors"][0]["type"] == "CONFIG_INVALID"


def test_validation_errors_name_the_field_not_the_value(write_bundle, make_client) -> None:
    # Evaluation API 0.8 (gap L1): location and type only; content never comes back.
    write_bundle(bundle_yaml(), active=1)
    client = make_client()
    missing = _request()
    del missing["timestamp"]
    wrong_type = _request()
    wrong_type["content"]["messages"][0]["content"] = 10000000146  # a number, not text
    content_as_key = _request()
    content_as_key["content"]["TC 10000000146"] = "x"  # an unknown key that is itself content
    content_as_key["content"]["metadata"] = {"10000000146": "y", "ok": 1}
    for body, loc, kind in (
        (missing, ["body", "timestamp"], "missing"),
        (wrong_type, ["body", "content", "messages", 0, "content"], "string_type"),
        (content_as_key, ["body", "content", "<key>"], "extra_forbidden"),
    ):
        response = client.post("/v1/evaluate", json=body)
        assert response.status_code == 422
        parsed = ValidationErrorBody.model_validate(response.json())
        assert parsed.detail.error == "invalid_request"
        assert {"loc": loc, "type": kind} in [p.model_dump() for p in parsed.detail.problems]
        assert "10000000146" not in response.text and '"input"' not in response.text


def test_invalid_json_is_422_without_the_body(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    response = make_client().post(
        "/v1/evaluate",
        content=b'{"content": "TC kimlik 10000000146"',  # truncated JSON
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    problems = ValidationErrorBody.model_validate(response.json()).detail.problems
    assert problems[0].type == "json_invalid"
    assert "10000000146" not in response.text


def test_tenant_key_required_outside_development(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    client = make_client(own_detector=True, env="test", require_signed_bundles=False)
    body = client.post("/v1/evaluate", json=_request()).json()
    assert body["decision"]["action"] == "BLOCK"
    assert body["errors"][0] == {"layer": "L0", "type": "TENANT_KEY_MISSING", "retryable": False}


def test_tenant_key_derived_from_the_master_key(write_bundle, make_client) -> None:
    # A tenant created at runtime (an organization) has no explicit key (ADR-0032 amendment).
    write_bundle(bundle_yaml(), active=1)
    master = base64.b64encode(bytes(range(32))).decode()
    client = make_client(
        own_detector=True, env="test", require_signed_bundles=False, tenant_hmac_master_key=master
    )
    body = client.post("/v1/evaluate", json=_request()).json()
    assert not body.get("errors") and body["decision"]["action"] == "BLOCK"


def test_detect_endpoint(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    response = make_client().post("/v1/detect", json=_request())
    body = response.json()
    assert body["redacted_text"] == "TC kimlik <TR_TCKN_1> ve IBAN <TR_IBAN_1>"
    assert [e["type"] for e in body["entities"]] == ["TR_TCKN", "TR_IBAN"]
    assert "value_hash" not in response.text and "10000000146" not in response.text


def test_detect_endpoint_hidden_outside_development(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    client = make_client(
        env="test", require_signed_bundles=False, tenant_hmac_keys={"tenant-a": "a2V5"}
    )
    assert client.post("/v1/detect", json=_request()).status_code == 404


def test_inline_bundle_gate(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    inline = yaml.safe_load(bundle_yaml(bundle_id="gr-inline"))
    dev = make_client()
    body = dev.post("/v1/evaluate", json=_request(bundle_inline=inline)).json()
    assert body["bundle"]["id"] == "gr-inline"
    bad = dict(inline, l0={"entities": ["TR_PASSPORT"]})
    body = dev.post("/v1/evaluate", json=_request(bundle_inline=bad)).json()
    assert body["errors"][0]["type"] == "CONFIG_INVALID"
    prod_like = make_client(
        env="test", require_signed_bundles=False, tenant_hmac_keys={"tenant-a": "a2V5"}
    )
    assert prod_like.post("/v1/evaluate", json=_request(bundle_inline=inline)).status_code == 400


def test_bundles_status_and_live_switch(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(version=1), active=1)
    write_bundle(bundle_yaml(version=2, l0_extra=MASK_EMAIL), version=2)
    client = make_client()
    status = client.get("/v1/bundles").json()
    assert status["bundles"][0]["active_version"] == 1
    assert [v["version"] for v in status["bundles"][0]["versions"]] == [1, 2]
    email = _request("mail ali@example.com")
    assert client.post("/v1/evaluate", json=email).json()["decision"]["action"] == "BLOCK"

    pointer = write_bundle.root / "gr-test" / "active"
    pointer.write_text("2")
    _touch(pointer)
    assert client.post("/v1/bundles/reload").json() == {"changed": True}
    body = client.post("/v1/evaluate", json=email).json()
    assert body["bundle"]["version"] == 2
    assert body["decision"]["action"] == "ALLOW_WITH_MODIFICATIONS"


def test_malformed_request_is_422(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    bad = _request()
    bad["unexpected"] = True
    assert make_client().post("/v1/evaluate", json=bad).status_code == 422


# ------------------------------------------------------------------ API token (ADR-0016)


def test_api_token_required_on_v1_routes(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    client = make_client(api_token=API_TOKEN)  # sends the right token by default
    assert client.post("/v1/evaluate", json=_request(CLEAN_TEXT)).status_code == 200

    for headers in (
        {"Authorization": ""},
        {"Authorization": "Bearer wrong"},
        {"Authorization": f"Basic {API_TOKEN}"},
    ):
        response = client.post("/v1/evaluate", json=_request(CLEAN_TEXT), headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert API_TOKEN not in response.text
    for path in ("/v1/bundles",):
        assert client.get(path, headers={"Authorization": ""}).status_code == 401
    assert client.post("/v1/bundles/reload", headers={"Authorization": ""}).status_code == 401
    assert (
        client.post("/v1/detect", json=_request(), headers={"Authorization": ""}).status_code == 401
    )


def test_health_routes_stay_open(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    client = make_client(api_token=API_TOKEN)
    assert client.get("/healthz", headers={"Authorization": ""}).status_code == 200
    assert client.get("/readyz", headers={"Authorization": ""}).status_code == 200


def test_development_without_token_is_open(write_bundle, make_client) -> None:
    write_bundle(bundle_yaml(), active=1)
    assert make_client().get("/v1/bundles").status_code == 200


def test_refuses_to_start_without_token_outside_development(write_bundle, detector) -> None:
    write_bundle(bundle_yaml(), active=1)
    settings = Settings(
        env="test", policy_dir=write_bundle.root, bundle_poll_interval_s=0, l0_threads=1
    )
    with (
        pytest.raises(RuntimeError, match="UMAI_DE_API_TOKEN"),
        TestClient(create_app(settings, detector=detector)),
    ):
        pass


# ------------------------------------------------------------------ guardrail view and provenance


POLICY_BUNDLE = """\
contract_version: "0.6"
id: gr-test
version: 1
name: Test guardrail
mode: ENFORCE
phases: [PRE_LLM, TOOL_INPUT]
l0:
  policies:
    - {id: ids, version: 2, name: Kimlik, entities: [TR_TCKN], action: BLOCK}
    - {id: contact, entities: [EMAIL_ADDRESS], action: ALLOW_WITH_MODIFICATIONS, roles: [user]}
"""


def test_bundle_version_endpoint(write_bundle, make_client) -> None:
    write_bundle(POLICY_BUNDLE, active=1)
    client = make_client()
    body = client.get("/v1/bundles/gr-test/versions/1").json()
    assert body["active"] is True and len(body["sha256"]) == 64
    assert body["bundle"]["name"] == "Test guardrail"
    assert [p["id"] for p in body["bundle"]["l0"]["policies"]] == ["ids", "contact"]
    assert client.get("/v1/bundles/gr-test/versions/9").status_code == 404
    assert client.get("/v1/bundles").json()["bundles"][0]["versions"][0]["name"] == "Test guardrail"


def test_policy_provenance_and_roles(write_bundle, make_client) -> None:
    write_bundle(POLICY_BUNDLE, active=1)
    client = make_client()
    blocked = client.post(
        "/v1/evaluate", json=_request("TC 10000000146, mail ali@example.com")
    ).json()
    assert blocked["decision"]["reason"] == "L0: TR_TCKN detected by policy Kimlik v2"
    assert [(m["id"], m["action"]) for m in blocked["decision"]["matched"]] == [
        ("ids", "BLOCK"),
        ("contact", "ALLOW_WITH_MODIFICATIONS"),
    ]
    # the contact policy screens the user role only: a tool call may carry an e-mail
    tool = client.post(
        "/v1/evaluate", json=_request("send to ali@example.com", phase="TOOL_INPUT")
    ).json()
    assert tool["decision"]["action"] == "ALLOW" and tool["entities"] == []
