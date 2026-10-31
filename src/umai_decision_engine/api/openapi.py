"""OpenAPI metadata for the customer surfaces and the public documents built from it (ADR-0054).

The engine API and the gateway describe their routes in code (summaries, descriptions, error
bodies, examples). :func:`install` adds what FastAPI cannot see from the dependencies: the
bearer key on every ``/v1`` route. :func:`public_document` keeps only the allow-listed customer
operations, so a new route is never published by accident: policy-service and operator routes,
``/v1/detect`` (development) and the bundle routes stay out.

Examples use synthetic values only (AGENTS rule 2; ``docs/reference/turkish-identifiers.md``).
"""

from __future__ import annotations

import copy
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from umai_decision_engine.contracts.evaluation import (
    CONTRACT_VERSION,
    ErrorBody,
    InputTooLargeBody,
    QuotaExceededBody,
    ValidationErrorBody,
)

SECURITY_SCHEME = "UmaiKey"
INTERNAL = "x-umai-internal"  # a property of a development-only feature (json_schema_extra)

# The operations each public document contains; everything else is internal.
PUBLIC_OPERATIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "evaluation-api": {"/v1/evaluate": ("post",)},
    "gateway-api": {
        "/v1/chat/completions": ("post",),
        "/v1/responses": ("post",),
        "/v1/embeddings": ("post",),
        "/v1/models": ("get",),
    },
}

SERVERS = [
    {
        "url": "https://{host}",
        "description": "Your UMAI address; the console shows it under API & SDKs.",
        "variables": {"host": {"default": "umai.example"}},
    }
]


def install(app: FastAPI) -> None:
    """Build ``app.openapi()`` with the API key declared: every ``/v1`` route requires
    ``Authorization: Bearer <key>`` (evaluation API 0.6, gateway API)."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = get_openapi(
                title=app.title,
                version=app.version,
                summary=app.summary,
                description=app.description,
                routes=app.routes,
                tags=app.openapi_tags,
            )
            schema.setdefault("components", {}).setdefault("securitySchemes", {})[
                SECURITY_SCHEME
            ] = {
                "type": "http",
                "scheme": "bearer",
                "description": "An API key from the console: `umai_<env>_<region>_<key_id>_"
                "<secret>`. Server side only; never in a browser or a mobile app.",
            }
            for path, operations in schema.get("paths", {}).items():
                if path.startswith("/v1/"):
                    for operation in operations.values():
                        operation.setdefault("security", [{SECURITY_SCHEME: []}])
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]


def public_document(schema: dict[str, Any], document: str) -> dict[str, Any]:
    """The published OpenAPI document ``document`` (a key of :data:`PUBLIC_OPERATIONS`): the
    allow-listed operations, without development-only properties and unreferenced components."""

    allowed = PUBLIC_OPERATIONS[document]
    result = copy.deepcopy(schema)
    paths: dict[str, Any] = {}
    for path, methods in allowed.items():
        operations = schema.get("paths", {}).get(path, {})
        missing = [m for m in methods if m not in operations]
        if missing:
            raise KeyError(f"{document}: {path} has no {', '.join(missing)} operation")
        paths[path] = {m: copy.deepcopy(operations[m]) for m in methods}
    result["paths"] = paths
    result["servers"] = copy.deepcopy(SERVERS)
    schemas = result.get("components", {}).get("schemas", {})
    for definition in schemas.values():
        _drop_internal(definition)
    used = _referenced(paths, schemas)
    result.setdefault("components", {})["schemas"] = {
        name: definition for name, definition in schemas.items() if name in used
    }
    return result


def _drop_internal(definition: dict[str, Any]) -> None:
    properties = definition.get("properties", {})
    internal = [name for name, prop in properties.items() if prop.get(INTERNAL)]
    for name in internal:
        del properties[name]
    if internal and "required" in definition:
        definition["required"] = [n for n in definition["required"] if n not in internal]


def _referenced(paths: dict[str, Any], schemas: dict[str, Any]) -> set[str]:
    prefix = "#/components/schemas/"
    found: set[str] = set()
    pending: list[Any] = [paths]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith(prefix):
                name = ref.removeprefix(prefix)
                if name not in found:
                    found.add(name)
                    pending.append(schemas.get(name, {}))
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return found


# ------------------------------------------------------------------ the evaluation API


def _example_request(phase: str, messages: list[dict[str, str]], focus: str) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": "req-7d2c41",
        "timestamp": "2026-10-31T09:30:00Z",
        "phase": phase,
        "content": {"messages": messages, "focus": focus, "language": "tr"},
        "subject": {"user_id": "u-5b81", "session_id": "s-0192"},
        "destination": {"app": "support-assistant", "class": "external_llm"},
    }


EVALUATE_EXAMPLES: dict[str, Any] = {
    "prompt": {
        "summary": "Check a prompt before it goes to a model",
        "value": _example_request(
            "PRE_LLM",
            [{"role": "user", "content": "10000000146 numaralı müşterinin kredi limitini özetle."}],
            "LAST_USER_MESSAGE",
        ),
    },
    "answer": {
        "summary": "Check a model's answer before you show it",
        "value": _example_request(
            "POST_LLM",
            [
                {"role": "user", "content": "Kart numarası nasıl görünür?"},
                {"role": "assistant", "content": "Örnek bir kart numarası: 4242424242424242"},
            ],
            "LAST_ASSISTANT_MESSAGE",
        ),
    },
}

_BUNDLE = {
    "id": "gr-demo-data-protection",
    "version": 4,
    "mode": "ENFORCE",
    "sha256": "70f8e248132ea25c5a7496e6c2e5d3bbad7d354d088fa9478e6a228795b161a2",
}


def _answer(decision: dict[str, Any], entity: str, tag: str, score: float) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": "req-7d2c41",
        "tenant_id": "org-3f9a",
        "bundle": _BUNDLE,
        "phase": "PRE_LLM",
        "decision": decision,
        "degraded": False,
        "tags": [{"code": tag, "layer": "L0", "score": score}],
        "entities": [{"type": entity, "count": 1, "distinct": 1}],
        "event_id": "ta-195c97496f4748b88442e60f6ed6e1b9",
        "review_queued": False,
        "latency_ms": {"total": 5.5, "l0": 5.3},
        "errors": [],
    }


_BLOCKED = _answer(
    {
        "action": "BLOCK",
        "allowed": False,
        "severity": "HIGH",
        "reason": "L0: TR_TCKN detected by policy Kimlik verileri v2",
        "decided_by": "L0",
        "matched": [
            {
                "kind": "l0_policy",
                "id": "identity-data",
                "version": 2,
                "name": "Kimlik verileri",
                "action": "BLOCK",
                "entities": ["TR_TCKN"],
            }
        ],
    },
    "TR_TCKN",
    "data.pii.tr_tckn",
    0.6,
)

_MASKED = {
    **_answer(
        {
            "action": "ALLOW_WITH_MODIFICATIONS",
            "allowed": True,
            "severity": "MEDIUM",
            "reason": "L0: EMAIL_ADDRESS detected by policy İletişim bilgileri v1",
            "decided_by": "L0",
            "matched": [
                {
                    "kind": "l0_policy",
                    "id": "contact-data",
                    "version": 1,
                    "name": "İletişim bilgileri",
                    "action": "ALLOW_WITH_MODIFICATIONS",
                    "entities": ["EMAIL_ADDRESS"],
                }
            ],
        },
        "EMAIL_ADDRESS",
        "data.pii.email",
        0.9,
    ),
    "output_modifications": {
        "redacted_text": "Bu e-postayı <EMAIL_ADDRESS_1> adresine gönder.",
        "entities": ["EMAIL_ADDRESS"],
    },
}


def _error(code: str, detail: str) -> dict[str, Any]:
    return {"value": {"detail": {"error": code, "detail": detail}}}


EVALUATE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The decision. Every request that passed admission gets one, including "
        "when a part of the evaluation failed (`degraded: true`).",
        "content": {
            "application/json": {
                "examples": {
                    "blocked": {"summary": "Blocked: an identity number", "value": _BLOCKED},
                    "masked": {
                        "summary": "Allowed with an e-mail address masked",
                        "value": _MASKED,
                    },
                }
            }
        },
    },
    400: {
        "model": ErrorBody,
        "description": "`tenant_required`: only for the self-hosted operator token.",
    },
    401: {
        "model": ErrorBody,
        "description": "`invalid_credential`: missing, unknown, expired or revoked key.",
        "content": {
            "application/json": {
                "examples": {
                    "invalid_credential": _error(
                        "invalid_credential", "missing or invalid API credential"
                    )
                }
            }
        },
    },
    403: {
        "model": ErrorBody,
        "description": "`ip_not_allowed`, `insufficient_scope`, `tenant_mismatch`, "
        "`guardrail_mismatch` or `unknown_application`.",
        "content": {
            "application/json": {
                "examples": {
                    "ip_not_allowed": _error(
                        "ip_not_allowed", "this key may not be used from this address"
                    ),
                    "insufficient_scope": _error(
                        "insufficient_scope", "scope evaluate is required"
                    ),
                }
            }
        },
    },
    413: {
        "model": InputTooLargeBody,
        "description": "`input_too_large`: the content is longer than your limit. Nothing was "
        "evaluated and nothing was cut.",
    },
    422: {
        "model": ValidationErrorBody | ErrorBody,
        "description": "`invalid_request`: the body does not match the contract (where and "
        "how, never what it said); `bundle_required`: a key without an application must "
        "name the guardrail.",
    },
    429: {
        "model": QuotaExceededBody | ErrorBody,
        "description": "`rate_limited`, `too_many_failed_attempts` or `quota_exceeded`. Wait "
        "for `Retry-After` seconds.",
        "headers": {
            "Retry-After": {
                "description": "Seconds to wait before the next attempt.",
                "schema": {"type": "integer"},
            }
        },
    },
    503: {"description": "The service is starting; retry shortly."},
}

EVALUATE_OPENAPI_EXTRA: dict[str, Any] = {
    "requestBody": {"content": {"application/json": {"examples": EVALUATE_EXAMPLES}}}
}
