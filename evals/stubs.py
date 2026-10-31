"""Stub servers for measurements on a local stack (S0-2, S0-3): an OpenAI-compatible upstream
model and an OpenAI Decisions API provider, in one ASGI app, so the gateway's forwarding and the
L1b adapter run their real HTTP paths without a network or a key. Answers are synthetic and fixed;
nothing the stub receives is stored or logged.

    uvicorn evals.stubs:app --port <port>     # UMAI_STUB_LATENCY_MS: added before every answer
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ANSWER = (
    "Toplantı notlarının özeti: bütçe onaylandı, teslim tarihi iki hafta ileri alındı ve "
    "tedarikçi sözleşmesi hukuk ekibine gönderildi. Bir sonraki adım, ekiplerin güncel planı "
    "cuma gününe kadar paylaşması. "
) * 2  # ~400 characters, no identifiers

app = FastAPI(title="umai-eval-stubs", docs_url=None, redoc_url=None, openapi_url=None)


def _latency_s() -> float:
    return float(os.environ.get("UMAI_STUB_LATENCY_MS", "0")) / 1000


async def _wait() -> None:
    latency = _latency_s()
    if latency > 0:
        await asyncio.sleep(latency)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": "stub-model", "object": "model", "owned_by": "stub"}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse:
    body = await request.json()
    await _wait()
    return JSONResponse(
        {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "stub-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ANSWER},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 250, "completion_tokens": 100, "total_tokens": 350},
        }
    )


def _answer(question: dict[str, Any]) -> dict[str, Any]:
    """A benign answer of the question's type: low probability, level 0, the last choice (the
    demo guardrail's policies list the harmless option last: ``no_injection``, ``general``)."""

    name = question.get("name")
    kind = question.get("type")
    if kind == "choice":
        values = [c["value"] for c in question.get("choices", [])]
        rest = (1 - 0.94) / max(1, len(values) - 1)
        return {
            "type": "choice",
            "name": name,
            "choice": values[-1],
            "probabilities": [
                {"value": v, "probability": 0.94 if v == values[-1] else rest} for v in values
            ],
        }
    if kind == "score":
        levels = question.get("levels", [])
        return {
            "type": "score",
            "name": name,
            "score": 0,
            "probabilities": [
                {"value": i, "label": lv.get("label"), "probability": 0.9 if i == 0 else 0.0}
                for i, lv in enumerate(levels)
            ],
        }
    return {"type": "predicate", "name": name, "probability": 0.03}


@app.post("/v1/decisions")
async def decisions(request: Request) -> JSONResponse:
    body = await request.json()
    await _wait()
    return JSONResponse(
        {
            "id": "dec-stub",
            "model": body.get("model", "stub-decisions"),
            "answers": [_answer(q) for q in body.get("questions", [])],
            "usage": {"input_tokens": 900, "output_tokens": 20, "total_tokens": 920},
        }
    )
