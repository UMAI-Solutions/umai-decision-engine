"""HTTP 422 without the caller's input on the policy service (launch plan S0-4, organizations
0.6), as the engine API answers since evaluation API 0.8.

FastAPI's default 422 body repeats the offending value (``input``, ``ctx``, sometimes ``msg``):
a guardrail draft, a golden case, a search term or billing details would come back in the error
and in anything that logs it. The answer names where the request is malformed and how, never
what it said. A location keeps only the service's own field and parameter names (read from its
OpenAPI schema) and list positions; any other key is shown as ``<key>``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from umai_decision_engine.contracts.evaluation import (
    ValidationErrorBody,
    ValidationErrorDetail,
    ValidationProblem,
)

MAX_PROBLEMS = 50
OTHER_KEY = "<key>"
_ROUTE_PARTS = frozenset({"body", "query", "path", "header", "cookie"})


def field_names(app: FastAPI) -> frozenset[str]:
    """Every property and parameter name the app's own schema declares."""

    schema = app.openapi()
    names = set(_ROUTE_PARTS)
    for definition in schema.get("components", {}).get("schemas", {}).values():
        names.update(definition.get("properties", {}))
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            if isinstance(operation, dict):
                names.update(p["name"] for p in operation.get("parameters", []) if "name" in p)
    return frozenset(names)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    state = request.app.state
    names = getattr(state, "field_names", None)
    if names is None:
        names = state.field_names = field_names(request.app)
    problems = [
        ValidationProblem(
            loc=[
                part if isinstance(part, int) or part in names else OTHER_KEY
                for part in error.get("loc", ())
            ],
            type=str(error.get("type", "invalid")),
        )
        for error in exc.errors()[:MAX_PROBLEMS]
    ]
    body = ValidationErrorBody(
        detail=ValidationErrorDetail(
            detail="the request does not match the policy service's contract", problems=problems
        )
    )
    return JSONResponse(status_code=422, content=body.model_dump(mode="json"))
