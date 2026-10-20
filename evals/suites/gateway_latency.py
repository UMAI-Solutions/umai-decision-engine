"""Suite `gateway-latency`: what the UMAI gateway adds to a call (ADR-0034, AGENTS rule 6).

The gateway runs in-process against a zero-latency fake upstream, so every millisecond measured is
the gateway's own: admission, gateway and model, input check (L0 over every message), masking,
answer check, restoration, JSON and SSE handling. Guardrail: the repository's demo bundle (full
L0 catalog). Synthetic text only. No network.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from evals.common import ROOT, report_dir, run_info
from umai_decision_engine.gateway.app import create_gateway_app
from umai_decision_engine.settings import Settings

NAME = "gateway-latency"
BUNDLE = "gr-demo-data-protection"
RUNS = 200

SENTENCE = (
    "Toplantıda bütçe, teslim tarihleri ve tedarikçi sözleşmeleri konuşuldu; ekip bir sonraki "
    "adımları netleştirdi. "
)
PII = (
    "Müşteri Ayşe Demir, telefon 0532 123 45 67, e-posta ayse@example.com. "  # masked, not blocked
)


def _text(chars: int, *, pii: bool) -> str:
    unit = SENTENCE * 3 + (PII if pii else SENTENCE)
    return (unit * (chars // len(unit) + 1))[:chars]


def _completion(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-bench",
        "object": "chat.completion",
        "created": 0,
        "model": "bench",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _stream(answer: str, piece: int) -> bytes:
    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        body = {
            "id": "chatcmpl-bench",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "bench",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(body)}\n\n"

    parts = [chunk({"role": "assistant", "content": ""})]
    parts += [chunk({"content": answer[i : i + piece]}) for i in range(0, len(answer), piece)]
    parts += [chunk({}, "stop"), "data: [DONE]\n\n"]
    return "".join(parts).encode()


def _percentiles(samples: list[float]) -> tuple[float, float, float]:
    ordered = sorted(samples)
    q = statistics.quantiles(ordered, n=100, method="inclusive")
    return q[49], q[94], q[98]


def run(args: argparse.Namespace) -> Path:
    info = run_info(NAME, "all", args.tag, Path(__file__))
    answer = _text(2_000, pii=False)
    state: dict[str, Any] = {"reply": _completion(answer[:400]), "stream": _stream(answer, 40)}

    def upstream(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("stream"):
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=state["stream"]
            )
        return httpx.Response(200, json=state["reply"])

    work = Path(tempfile.mkdtemp(prefix="umai-gw-bench-"))
    applications = work / "applications"
    applications.mkdir()
    # One application per streaming mode; the model name doubles as the application's id. The
    # built-in OpenAI provider is replaced by one without a credential (the fake upstream).
    content = {
        "providers": [{"provider_id": "openai", "kind": "openai", "credential": "none"}],
        "applications": [
            {
                "application_id": f"r-{mode}",
                "guardrail": BUNDLE,
                "aliases": [{"name": f"r-{mode}", "model": "m"}],
                "stream_mode": mode,
            }
            for mode in ("window", "buffer", "after")
        ],
    }
    (applications / "default.yaml").write_text(json.dumps(content), encoding="utf-8")
    settings = Settings(
        env="development",
        policy_dir=ROOT / "policies",
        applications_dir=applications,
        applications_poll_interval_s=0,
        bundle_poll_interval_s=0,
        l0_threads=1,
        log_level="WARNING",
        rate_limit_rps=None,  # measure the gateway, not the per-key limiter
    )
    app = create_gateway_app(settings, upstream_transport=httpx.MockTransport(upstream))
    direct = httpx.Client(transport=httpx.MockTransport(upstream))

    def body(messages: list[dict[str, str]], **extra: Any) -> dict[str, Any]:
        return {"model": "r-window", "messages": messages, **extra}

    history = []
    for i in range(16):
        role = "user" if i % 2 == 0 else "assistant"
        history.append({"role": role, "content": _text(500, pii=i % 4 == 0)})
    scenarios: list[tuple[str, Any]] = [
        ("fake upstream called directly (baseline)", None),
        (
            "500 chars, clean, non-streaming",
            body([{"role": "user", "content": _text(500, pii=False)}]),
        ),
        (
            "4 000 chars with identifiers (masked), non-streaming",
            body([{"role": "user", "content": _text(4_000, pii=True)}]),
        ),
        ("16-message history, 8 000 chars, identifiers masked", body(history)),
        (
            "streaming `window`, 2 000-char answer in 40-char chunks",
            body([{"role": "user", "content": "Uzun yaz"}], stream=True),
        ),
        (
            "streaming `buffer`, same answer",
            {**body([{"role": "user", "content": "Uzun yaz"}], stream=True), "model": "r-buffer"},
        ),
        (
            "streaming `after`, same answer",
            {**body([{"role": "user", "content": "Uzun yaz"}], stream=True), "model": "r-after"},
        ),
    ]
    rows: list[str] = []
    window_note = ""
    with TestClient(app) as client:
        for label, payload in scenarios:

            def call(payload: Any = payload) -> httpx.Response:
                if payload is None:
                    return direct.post(
                        "https://api.openai.com/v1/chat/completions",
                        json={"model": "m", "messages": []},
                    )
                return client.post(
                    "/v1/chat/completions",
                    json=payload,
                    headers={"X-Umai-Application": payload["model"]},
                )

            for _ in range(5):
                response = call()  # warm-up
                assert response.status_code == 200, (label, response.status_code)
            samples = []
            for _ in range(RUNS):
                started = time.perf_counter()
                response = call()
                samples.append((time.perf_counter() - started) * 1000)
            p50, p95, p99 = _percentiles(samples)
            rows.append(f"| {label} | {p50:.2f} ms | {p95:.2f} ms | {p99:.2f} ms |")
            if payload is not None and payload.get("stream") and payload["model"] == "r-window":
                events = [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: {")
                ]
                contents = [
                    c["choices"][0]["delta"]["content"]
                    for c in events
                    if c.get("choices") and c["choices"][0]["delta"].get("content")
                ]
                first = len(contents[0]) + settings.gateway_stream_holdback_chars
                window_note = (
                    f"`window` released the answer in {len(contents)} content chunks; the first "
                    f"text left after about {first} upstream characters (hold-back "
                    f"{settings.gateway_stream_holdback_chars}, step "
                    f"{settings.gateway_stream_step_chars}). At 4 characters per token that is "
                    f"about {first // 4} tokens: ≈ {first / 4 / 100:.2f} s at 100 tokens/s, "
                    f"≈ {first / 4 / 40:.2f} s at 40 tokens/s before the user sees the first words."
                )

    directory = report_dir(info)
    lines = [
        "# Gateway latency — what the gateway adds to a call",
        "",
        f"- **Suite:** `{NAME}` (in-process gateway, zero-latency fake upstream, {RUNS} runs "
        "per row after 5 warm-up calls, one client at a time)",
        *info.header_lines(),
        f"- **Guardrail:** `{BUNDLE}` (active version; full L0 catalog), L0 on 1 thread",
        "",
        "| Scenario | p50 | p95 | p99 |",
        "|---|---|---|---|",
        *rows,
        "",
        window_note,
        "",
        "**Reading.** With a zero-latency upstream the numbers are the gateway's own cost per call "
        "(HTTP handling in the test client included). Real upstream latency (hundreds of "
        "milliseconds to seconds) comes on top. Not measured: concurrency, the reference on-prem "
        "hardware, injection detection (WP4).",
        "",
    ]
    (directory / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return directory
