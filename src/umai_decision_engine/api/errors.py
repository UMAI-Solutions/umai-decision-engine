"""HTTP 422 without the caller's input (gap L1, evaluation API 0.8).

FastAPI's default 422 body repeats the offending value (`input`, `ctx`, sometimes `msg`), and on
`/v1/evaluate` that value can be the very content the engine protects (ADR-0005). The answer
names where the request is malformed and how, never what it said. A location keeps only the
contract's own field names and list positions: a caller-chosen key (an unknown field, a
`metadata` key) is content too and becomes `<key>`.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from umai_decision_engine.contracts.evaluation import (
    EvaluationRequest,
    ValidationErrorBody,
    ValidationErrorDetail,
    ValidationProblem,
)

MAX_PROBLEMS = 50
OTHER_KEY = "<key>"
_ROUTE_PARTS = {"body", "query", "path", "header", "cookie", "bundle_id", "version"}


def _contract_names() -> frozenset[str]:
    names = set(_ROUTE_PARTS)
    for by_alias in (True, False):
        schema = EvaluationRequest.model_json_schema(by_alias=by_alias)
        for definition in [schema, *schema.get("$defs", {}).values()]:
            names.update(definition.get("properties", {}))
    return frozenset(names)


_NAMES = _contract_names()


def _loc_part(part: object) -> str | int:
    if isinstance(part, int):
        return part
    return part if isinstance(part, str) and part in _NAMES else OTHER_KEY


def problems(exc: RequestValidationError) -> list[ValidationProblem]:
    return [
        ValidationProblem(
            loc=[_loc_part(part) for part in error.get("loc", ())],
            type=str(error.get("type", "invalid")),
        )
        for error in exc.errors()[:MAX_PROBLEMS]
    ]


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    body = ValidationErrorBody(
        detail=ValidationErrorDetail(
            detail="the request does not match the evaluation API contract",
            problems=problems(exc),
        )
    )
    return JSONResponse(status_code=422, content=body.model_dump(mode="json"))
