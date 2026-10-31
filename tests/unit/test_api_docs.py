"""Customer API documentation (ADR-0054): the published OpenAPI documents match the apps and the
contract versions, publish customer routes only, and the pages cover every error code in both
languages with examples that parse."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import get_args

import pytest

from umai_decision_engine.api.app import API_VERSION as EVALUATION_API_VERSION
from umai_decision_engine.api.openapi import PUBLIC_OPERATIONS
from umai_decision_engine.contracts.evaluation import (
    CONTRACT_VERSION,
    ErrorBody,
    EvaluationRequest,
    EvaluationResponse,
    InputTooLargeBody,
    QuotaExceededBody,
    ValidationErrorBody,
)
from umai_decision_engine.contracts.gateway import GatewayErrorBody
from umai_decision_engine.gateway.app import API_VERSION as GATEWAY_API_VERSION
from umai_decision_engine.l1.decision_models.base import ErrorKind

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "umai_decision_engine"
DOCS = ROOT / "docs"
API_DOCS = DOCS / "api"


def _load_builder():
    spec = importlib.util.spec_from_file_location("api_docs", ROOT / "dev" / "api_docs.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


api_docs = _load_builder()


def _text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


@pytest.fixture(scope="module")
def generated() -> dict[str, dict]:
    return api_docs.generate_openapi()


# ------------------------------------------------------------------ OpenAPI and contracts


@pytest.mark.parametrize("name", api_docs.DOCUMENTS)
def test_published_openapi_is_what_the_apps_generate(generated, name):
    path = API_DOCS / "openapi" / f"{name}.json"
    assert _text(path) == api_docs.render_json(generated[name]), (
        f"{path.name} is stale: run python dev/api_docs.py openapi"
    )


def _contract_version(document: str) -> str:
    header = re.search(r"\*\*Version:\*\* ([0-9.]+)", _text(DOCS / "contracts" / f"{document}.md"))
    assert header, f"{document}.md has no **Version:** line"
    return header.group(1)


def _index_version(document: str) -> str:
    row = re.search(
        rf"^\| \[{re.escape(document)}\]\({re.escape(document)}\.md\) \| ([0-9.]+) \|",
        _text(DOCS / "contracts" / "README.md"),
        re.M,
    )
    assert row, f"{document} is missing from docs/contracts/README.md"
    return row.group(1)


@pytest.mark.parametrize(
    ("name", "declared"),
    [("evaluation-api", EVALUATION_API_VERSION), ("gateway-api", GATEWAY_API_VERSION)],
)
def test_published_openapi_carries_the_contract_version(name, declared):
    published = json.loads(_text(API_DOCS / "openapi" / f"{name}.json"))
    assert published["info"]["version"] == declared == _contract_version(name), (
        f"bump API_VERSION in the app with docs/contracts/{name}.md, then regenerate"
    )
    assert _index_version(name) == declared


def test_the_request_version_customers_are_told_to_send_is_accepted():
    published = json.loads(_text(API_DOCS / "openapi" / "evaluation-api.json"))
    accepted = published["components"]["schemas"]["EvaluationRequest"]["properties"][
        "contract_version"
    ]["enum"]
    assert accepted[-1] == CONTRACT_VERSION
    for language in api_docs.LANGUAGES:
        for page in (API_DOCS / language).glob("*.md"):
            for sent in re.findall(r'"contract_version": "([0-9.]+)"', _text(page)):
                assert sent == CONTRACT_VERSION, f"{language}/{page.name} sends {sent}"


@pytest.mark.parametrize("name", api_docs.DOCUMENTS)
def test_only_customer_operations_are_published(name):
    published = json.loads(_text(API_DOCS / "openapi" / f"{name}.json"))
    operations = {
        (path, method) for path, methods in published["paths"].items() for method in methods
    }
    allowed = {(path, m) for path, methods in PUBLIC_OPERATIONS[name].items() for m in methods}
    assert operations == allowed
    text = json.dumps(published)
    for internal in (
        "/v1/detect",
        "/v1/bundles",
        "/v1/tenants",
        "/v1/applications",
        "/healthz",
        "bundle_inline",
        "PolicyBundle",
        "x-umai-internal",
        "bundles:reload",
    ):
        assert internal not in text, internal


def test_no_policy_service_route_is_published():
    from umai_decision_engine.control_plane.app import create_app as create_policy_service

    service_paths = set(create_policy_service().openapi()["paths"])
    for name in api_docs.DOCUMENTS:
        published = json.loads(_text(API_DOCS / "openapi" / f"{name}.json"))
        assert not service_paths & set(published["paths"])


def test_every_v1_operation_declares_the_api_key():
    for name in api_docs.DOCUMENTS:
        published = json.loads(_text(API_DOCS / "openapi" / f"{name}.json"))
        assert published["components"]["securitySchemes"]["UmaiKey"]["scheme"] == "bearer"
        for operations in published["paths"].values():
            for operation in operations.values():
                assert operation["security"] == [{"UmaiKey": []}]


def test_openapi_examples_match_the_contract_models():
    evaluation = json.loads(_text(API_DOCS / "openapi" / "evaluation-api.json"))
    operation = evaluation["paths"]["/v1/evaluate"]["post"]
    for example in operation["requestBody"]["content"]["application/json"]["examples"].values():
        EvaluationRequest.model_validate(example["value"])
    for status, response in operation["responses"].items():
        for example in (
            response.get("content", {}).get("application/json", {}).get("examples", {}).values()
        ):
            model = EvaluationResponse if status == "200" else ErrorBody
            model.model_validate(example["value"])
    gateway = json.loads(_text(API_DOCS / "openapi" / "gateway-api.json"))
    for operations in gateway["paths"].values():
        for operation in operations.values():
            for status, response in operation["responses"].items():
                if status == "200":
                    continue
                for example in (
                    response.get("content", {})
                    .get("application/json", {})
                    .get("examples", {})
                    .values()
                ):
                    GatewayErrorBody.model_validate(example["value"])


# ------------------------------------------------------------------ the error catalogue

# Error codes the customer surfaces can answer with, read from the code that raises them.
_CODE_PATTERNS = (
    re.compile(r'"error":\s*"([a-z_]+)"'),
    re.compile(r'GatewayHTTPError\(\s*\d+,\s*"([a-z_]+)"'),
    re.compile(r'ResolveError\(\s*\d+,\s*"([a-z_]+)"'),
    re.compile(r'error: Literal\["([a-z_]+)"\]'),
    re.compile(r'"(umai_[a-z_]+)" if'),
    re.compile(r'if step_up else "(umai_[a-z_]+)"'),
)
_SURFACES = {
    "evaluation-api": [
        "api/routes.py",
        "api/errors.py",
        "tenancy/admission.py",
        "contracts/evaluation.py",
    ],
    "gateway-api": ["gateway/", "tenancy/admission.py", "contracts/gateway.py"],
}
# Raised by code that never reaches a customer of that surface.
_NOT_CUSTOMER_FACING = {
    "request_refused",  # gateway/errors.py: fallback for an admission error without a code
}


def _emitted(surface: str) -> set[str]:
    codes: set[str] = set()
    for entry in _SURFACES[surface]:
        target = SRC / entry
        files = sorted(target.glob("*.py")) if entry.endswith("/") else [target]
        for path in files:
            text = _text(path)
            for pattern in _CODE_PATTERNS:
                codes.update(pattern.findall(text))
    return codes - _NOT_CUSTOMER_FACING


def _catalogue(language: str, section: int) -> set[str]:
    """Codes in the n-th HTTP error table of errors.md (0: evaluation API, 1: gateway)."""

    text = _text(API_DOCS / language / "errors.md")
    tables = re.split(r"^## ", text, flags=re.M)
    rows = [t for t in tables if re.search(r"^\| \d{3} \| `", t, re.M)]
    return set(re.findall(r"^\| \d{3} \| `([a-z_]+)`", rows[section], re.M))


def _decision_errors(language: str) -> set[str]:
    return set(re.findall(r"^\| `([A-Z0-9_]+)` \|", _text(API_DOCS / language / "errors.md"), re.M))


@pytest.mark.parametrize("language", ["en", "tr"])
def test_every_error_code_is_in_the_catalogue(language):
    assert _catalogue(language, 0) == _emitted("evaluation-api")
    # The gateway shares the engine's admission; the tenant checks are the evaluation API's
    # (the gateway takes the tenant from the key alone), and the quota refusal is built from the
    # evaluation contract's model.
    gateway = _emitted("gateway-api") - {"tenant_required", "tenant_mismatch"}
    assert _catalogue(language, 1) == gateway | {"quota_exceeded"}


def test_the_scan_finds_the_codes_it_should():
    # Guard against a pattern that silently stops matching.
    assert {"invalid_credential", "quota_exceeded", "input_too_large", "invalid_request"} <= (
        _emitted("evaluation-api")
    )
    assert {"umai_blocked", "umai_step_up_required", "model_not_allowed", "upstream_timeout"} <= (
        _emitted("gateway-api")
    )


@pytest.mark.parametrize("language", ["en", "tr"])
def test_every_decision_error_type_is_in_the_catalogue(language):
    orchestrator = _text(SRC / "pipeline" / "orchestrator.py")
    types = set(re.findall(r'type="([A-Z0-9_]+)"', orchestrator))
    l0_kinds = re.search(r"def _l0_error_kind.*?(?=\n\ndef |\Z)", orchestrator, re.S)
    assert l0_kinds is not None
    types |= set(re.findall(r'return "([A-Z0-9_]+)", (?:True|False)', l0_kinds.group(0)))
    types |= {f"L1B_{kind.upper()}" for kind in get_args(ErrorKind)}
    assert {"CONFIG_MISSING", "TIMEOUT", "L1B_TIMEOUT"} <= types
    assert _decision_errors(language) == types


# ------------------------------------------------------------------ pages and examples


def test_both_languages_have_the_same_pages_and_codes():
    en = {p.name for p in (API_DOCS / "en").glob("*.md")}
    tr = {p.name for p in (API_DOCS / "tr").glob("*.md")}
    assert en == tr == {f"{slug}.md" for slug in api_docs.PAGES}
    for name in en:
        codes = [
            re.findall(r"`([a-z_]+_[a-z_]+|[A-Z0-9_]{4,})`", _text(API_DOCS / lang / name))
            for lang in ("en", "tr")
        ]
        assert set(codes[0]) == set(codes[1]), name


def _blocks(kind: str) -> list[tuple[str, str]]:
    found = []
    for language in api_docs.LANGUAGES:
        for page in sorted((API_DOCS / language).glob("*.md")):
            for block in re.findall(rf"```{kind}\n(.*?)```", _text(page), re.S):
                found.append((f"{language}/{page.name}", block))
    return found


def test_json_examples_parse_with_the_contract_models():
    for where, block in _blocks("json"):
        value = json.loads(block)
        if "phase" in value and "content" in value and "decision" not in value:
            EvaluationRequest.model_validate(value)
        elif "decision" in value:
            EvaluationResponse.model_validate(value)
        elif "detail" in value:
            detail = value["detail"]
            model = {
                "input_too_large": InputTooLargeBody,
                "quota_exceeded": QuotaExceededBody,
                "invalid_request": ValidationErrorBody,
            }.get(detail["error"], ErrorBody)
            model.model_validate(value)
        elif "error" in value:
            GatewayErrorBody.model_validate(value)
        else:
            pytest.fail(f"{where}: an example the test does not know")


def test_curl_bodies_are_valid_requests():
    bodies = 0
    for where, block in _blocks("bash"):
        for body in re.findall(r"-d '(.*?)'", block, re.S):
            value = json.loads(body)
            bodies += 1
            if "phase" in value:
                EvaluationRequest.model_validate(value)
            else:
                assert {"model", "messages"} <= set(value), where
    assert bodies >= 6


def test_python_examples_are_python():
    blocks = _blocks("python")
    assert len(blocks) >= 6
    for where, block in blocks:
        try:
            ast.parse(block)
        except SyntaxError as exc:
            pytest.fail(f"{where}: {exc}")


def test_examples_use_synthetic_identifiers_only():
    # docs/reference/turkish-identifiers.md: valid synthetic test vectors
    allowed = {"10000000146", "4242424242424242", "TR330006100519786457841326"}
    for language in api_docs.LANGUAGES:
        for page in (API_DOCS / language).glob("*.md"):
            text = _text(page)
            assert set(re.findall(r"(?<!\d)\d{11}(?!\d)", text)) <= allowed, page
            assert set(re.findall(r"(?<!\d)\d{16}(?!\d)", text)) <= allowed, page
            for address in re.findall(r"[\w.]+@[\w.-]+", text):
                assert address.endswith("@example.com"), (page, address)


# ------------------------------------------------------------------ the site


def _digest(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(sorted(files.items())).encode()).hexdigest()


def test_site_builds_reproducibly_with_working_links(tmp_path):
    first = api_docs.site_files()  # raises BuildError on a broken link or a missing page
    assert _digest(first) == _digest(api_docs.site_files())
    for language, folder in (("en", ""), ("tr", "tr/")):
        for slug in (*api_docs.PAGES, *api_docs.REFERENCE):
            page = first[f"{folder}{slug}.html"]
            assert f'<html lang="{language}">' in page
    written = api_docs.build_site(tmp_path)
    assert len(written) == len(first)
    assert (tmp_path / "openapi" / "gateway-api.json").read_bytes().decode("utf-8") == (
        _text(API_DOCS / "openapi" / "gateway-api.json")
    )


def test_site_has_no_scripts_or_external_assets():
    for path, text in api_docs.site_files().items():
        if path.endswith(".html"):
            assert "<script" not in text, path
            assert not re.search(r'src="(?:https?:)?//', text), path
            assert not re.search(r'<link[^>]+href="(?:https?:)?//', text), path
