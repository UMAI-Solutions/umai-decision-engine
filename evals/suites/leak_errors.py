"""The leak suite's error paths (launch plan S0-4): no raw value in any 4xx or 5xx answer of the
engine API, the gateway or the policy service, nor in what they log while answering.

The three apps run in-process (one policy dir, key registry and applications dir, as on the
VM), the gateway against a scripted upstream. Every probe sends caller content made of a canary
(``CANARY``) and synthetic raw values of every entity type of the l0-pii-tr dataset, and is
refused or fails on purpose: every admission refusal (401, 403, 413, 429), malformed requests
(400, 422), guardrail refusals, upstream failures (502, 504), failure-policy decisions and
injected crashes (500); for the policy service additionally a walk over every route with a body
or query parameters, sending malformed and value-filled requests. A probe leaks when a raw value
(as written, or its digits when it has seven or more) is in the answer (body and headers) or in
a log record emitted while it ran (root logger at DEBUG), including the access line uvicorn
writes for the request. The canary marks caller content: it must not come back in an error the
engine wrote either. Provider errors the gateway passes through by contract (an error body with
``X-Umai-Upstream-Error``, an error event mid-stream) are reported apart: a provider can only
echo what it received, which is masked.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from evals.common import ROOT
from umai_decision_engine.api.app import create_app as create_engine_app
from umai_decision_engine.control_plane.app import create_app as create_control_app
from umai_decision_engine.gateway.app import create_gateway_app
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.settings import Settings
from umai_decision_engine.telemetry.logging import ACCESS_LOGGER
from umai_decision_engine.tenancy.limits import FailedAuthThrottle, RateLimiter

CANARY = "zx-canary-4417"
TENANT = "eval"
QUOTA_TENANT = "eval-quota"
BUNDLE = "gr-demo-data-protection"
CONTROL_TOKEN = "control-token-eval"  # synthetic
OUTSIDE = ("198.51.100.7", 50000)  # documentation address (RFC 5737), outside the key's network
# Types the demo guardrail masks (ALLOW_WITH_MODIFICATIONS): content made of these reaches the
# upstream, so upstream failures can be exercised; every other type blocks before it.
MASKED_TYPES = {"PERSON", "ADDRESS", "TR_PHONE", "PHONE_NUMBER", "EMAIL_ADDRESS", "IP_ADDRESS"}
_NON_DIGIT = re.compile(r"\D")
_PATH_PARAM = re.compile(r"\{([^}]+)\}")


def forms(value: str) -> set[str]:
    out = {value}
    digits = _NON_DIGIT.sub("", value)
    if len(digits) >= 7:
        out.add(digits)
    return out


@dataclass
class Values:
    """Synthetic raw values by entity type, from the dataset."""

    by_type: dict[str, list[str]]

    @classmethod
    def from_cases(cls, cases: list[dict], per_type: int = 2) -> Values:
        by_type: dict[str, list[str]] = {}
        for case in cases:
            for expected in case["expected"]:
                value = case["text"][expected["start"] : expected["end"]]
                bucket = by_type.setdefault(expected["type"], [])
                if len(bucket) < per_type and value not in bucket and len(value) >= 4:
                    bucket.append(value)
        return cls(by_type)

    def text(self, types: set[str] | None = None) -> str:
        chosen = [
            v for t, vs in sorted(self.by_type.items()) if types is None or t in types for v in vs
        ]
        return f"{CANARY} " + " | ".join(chosen)

    def found(self, blob: str) -> list[str]:
        return sorted(
            {t for t, vs in self.by_type.items() for v in vs if any(f in blob for f in forms(v))}
        )


@dataclass
class Probe:
    surface: str
    label: str
    status: int
    code: str
    response: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    echo: bool = False  # canary in the answer
    echo_logs: bool = False
    passthrough: bool = False  # a provider error body passed through (X-Umai-Upstream-Error)

    @property
    def leaked(self) -> bool:
        """A raw value or caller content in what UMAI itself answered or logged. A provider's
        error passed through can only hold what the provider received: masked content, plus any
        value L0 did not detect (an L0 miss, reported apart)."""

        own_answer = not self.passthrough and (bool(self.response) or self.echo)
        return own_answer or bool(self.logs) or self.echo_logs


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "httpx2" and "testserver" in record.getMessage():
            return  # the test client's own request line, not something a service logged
        text = self.format(record)
        if record.exc_info:
            text += "\n" + logging.Formatter().formatException(record.exc_info)
        self.messages.append(text)


def _code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "-"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("code") or error.get("type") or "-")
        detail = body.get("detail")
        if isinstance(detail, dict):
            return str(detail.get("error", "-"))
        if isinstance(detail, list):
            return "fastapi_validation"
        if isinstance(detail, str):
            return "detail_text"
        decision = body.get("decision")
        if isinstance(decision, dict):
            return f"decision:{decision.get('action')}"
    return "-"


class Upstream:
    """The gateway's upstream, scripted per probe. Error modes echo what they received."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.answer = "Özet hazır: toplantı notları ekte."

    def __call__(self, request: httpx.Request) -> httpx.Response:
        received = request.content.decode("utf-8", "replace")
        if self.mode == "timeout":
            raise httpx.ReadTimeout("upstream timed out", request=request)
        if self.mode == "connect":
            raise httpx.ConnectError("upstream unreachable", request=request)
        if self.mode in ("echo400", "echo500"):
            status = 400 if self.mode == "echo400" else 500
            body = {"error": {"message": f"cannot process: {received}", "type": "invalid_request"}}
            return httpx.Response(status, json=body)
        if self.mode == "garbage":
            return httpx.Response(200, content=f"<html>{received}</html>".encode())
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                    "model": "m",
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
            )
        stream = '"stream": true' in received or '"stream":true' in received
        if self.mode == "stream_error" or (stream and self.mode == "ok"):
            chunks = [{"role": "assistant", "content": ""}, {"content": self.answer}]
            events = [
                "data: "
                + json.dumps(
                    {
                        "id": "c",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": "m",
                        "choices": [{"index": 0, "delta": d, "finish_reason": None}],
                    }
                )
                + "\n\n"
                for d in chunks
            ]
            if self.mode == "stream_error":
                events.append(
                    "data: " + json.dumps({"error": {"message": f"failed on {received}"}}) + "\n\n"
                )
            events.append("data: [DONE]\n\n")
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content="".join(events).encode()
            )
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                json={
                    "id": "resp-1",
                    "object": "response",
                    "status": "completed",
                    "model": "m",
                    "output": [
                        {
                            "type": "message",
                            "id": "msg-1",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": self.answer}],
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": self.answer},
                        "finish_reason": "stop",
                    }
                ],
            },
        )


class Harness:
    def __init__(self, values: Values, detector: EntityDetector, work: Path) -> None:
        self.values = values
        self.detector = detector
        self.work = work
        self.capture = _Capture()
        self.capture.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        self.probes: list[Probe] = []
        self.upstream = Upstream()
        self.keys: dict[str, str] = {}

    # ---------------------------------------------------------------- apps

    def settings(self, **overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "env": "test",
            "policy_dir": self.work / "policies",
            "key_registry_dir": self.work / "keys",
            "applications_dir": self.work / "applications",
            "default_tenant_id": TENANT,
            "require_signed_bundles": False,
            "bundle_poll_interval_s": 0,
            "key_registry_poll_interval_s": 0,
            "applications_poll_interval_s": 0,
            "l0_threads": 1,
            "plans_file": ROOT / "deploy" / "plans.yaml",
            "control_api_token": CONTROL_TOKEN,
            "control_db_path": self.work / "control.sqlite",
            "decision_warm_up_timeout_ms": 0,
            "log_level": "DEBUG",
        }
        return Settings(_env_file=None, **(values | overrides))  # type: ignore[call-arg]

    def prepare(self) -> None:
        shutil.copytree(ROOT / "policies" / BUNDLE, self.work / "policies" / BUNDLE)
        (self.work / "applications").mkdir()
        content = {
            "providers": [
                {"provider_id": "openai", "kind": "openai", "credential": "none", "default": True},
                {
                    "provider_id": "pt",
                    "kind": "openai",
                    "base_url": "https://api.openai.com/v1",
                    "credential": "pass_through",
                },
            ],
            "applications": [
                {"application_id": "app", "guardrail": BUNDLE},
                {"application_id": "other", "guardrail": BUNDLE},
            ],
        }
        for tenant in (TENANT, QUOTA_TENANT):
            (self.work / "applications" / f"{tenant}.yaml").write_text(
                json.dumps(content), encoding="utf-8"
            )

    @contextlib.contextmanager
    def apps(self) -> Iterator[tuple[TestClient, TestClient, TestClient]]:
        control = TestClient(
            create_control_app(self.settings(), detector=self.detector),
            headers={"Authorization": f"Bearer {CONTROL_TOKEN}"},
            raise_server_exceptions=False,
        )
        engine = TestClient(
            create_engine_app(self.settings(), detector=self.detector),
            raise_server_exceptions=False,
        )
        gateway = TestClient(
            create_gateway_app(
                self.settings(),
                detector=self.detector,
                upstream_transport=httpx.MockTransport(self.upstream),
            ),
            raise_server_exceptions=False,
        )
        with control, engine, gateway:
            yield control, engine, gateway

    def mint(self, control: TestClient, engine: TestClient, gateway: TestClient) -> None:
        def key(name: str, tenant: str = TENANT, **body: Any) -> str:
            created = control.post(f"/v1/tenants/{tenant}/keys", json={"name": name, **body})
            assert created.status_code == 201, created.status_code
            return created.json()["secret"]

        for tenant in (TENANT, QUOTA_TENANT):
            control.post("/v1/tenants", json={"id": tenant})
        control.put(
            f"/v1/tenants/{QUOTA_TENANT}/plan",
            json={"edition": "community", "overrides": {"monthly_requests": 0}},
        )
        self.keys = {
            "full": key("full", scopes=["evaluate", "gateway"]),
            "evaluate": key("evaluate-only", scopes=["evaluate"]),
            "gateway": key("gateway-only", scopes=["gateway"]),
            "network": key(
                "network", scopes=["evaluate", "gateway"], allowed_ips=["203.0.113.0/24"]
            ),
            "app": key("app", application_id="app"),
            "quota": key("quota", tenant=QUOTA_TENANT, scopes=["evaluate", "gateway"]),
        }
        for app in (engine, gateway):
            app.app.state.admission.registry.reload(force=True)

    # ---------------------------------------------------------------- probing

    def probe(
        self,
        surface: str,
        label: str,
        send: Callable[[], httpx.Response],
        *,
        passthrough: bool = False,
    ) -> Probe:
        self.capture.messages.clear()
        response = send()
        # The access line uvicorn writes for this request (the test client has no uvicorn):
        # its call, with the path and the raw query string, through the configured filters.
        request = response.request
        logging.getLogger(ACCESS_LOGGER).info(
            '%s - "%s %s HTTP/%s" %d',
            "203.0.113.10:50000",
            request.method,
            request.url.raw_path.decode("ascii", "replace"),
            "1.1",
            response.status_code,
        )
        blob = response.text + "\n" + "\n".join(f"{k}: {v}" for k, v in response.headers.items())
        logs = "\n".join(self.capture.messages)
        upstream_error = response.headers.get("x-umai-upstream-error") == "true" or (
            response.headers.get("content-type", "").startswith("text/event-stream")
        )
        probe = Probe(
            surface,
            label,
            response.status_code,
            _code(response),
            response=self.values.found(blob),
            logs=self.values.found(logs),
            echo=CANARY in blob,
            echo_logs=CANARY in logs,
            passthrough=passthrough and upstream_error,
        )
        self.probes.append(probe)
        return probe

    def bearer(self, name: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.keys[name]}"}


def evaluate_body(text: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "contract_version": "0.12",
        "request_id": "leak-errors",
        "timestamp": "2026-10-31T10:00:00Z",
        "bundle": {"id": BUNDLE},
        "phase": "PRE_LLM",
        "content": {"messages": [{"role": "user", "content": text}], "focus": "LAST_USER_MESSAGE"},
    }
    body.update(overrides)
    return body


@contextlib.contextmanager
def patched(obj: Any, name: str, value: Any) -> Iterator[None]:
    previous = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, previous)


async def _crash(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("synthetic failure injected by the leak suite")


# -------------------------------------------------------------------- engine API


def engine_probes(h: Harness, engine: TestClient) -> None:
    text = h.values.text()
    big = (text + " ") * (32_000 // len(text) + 2)
    url = "/v1/evaluate"
    full = h.bearer("full")
    admission = engine.app.state.admission

    def post(body: Any = None, headers: dict | None = None, raw: bytes | None = None):
        return lambda: (
            engine.post(
                url, content=raw, headers={**(headers or full), "content-type": "application/json"}
            )
            if raw is not None
            else engine.post(url, json=body, headers=headers or full)
        )

    h.probe("engine", "401 no credential", post(evaluate_body(text), headers={}))
    h.probe(
        "engine",
        "401 bad credential",
        post(evaluate_body(text), headers={"Authorization": f"Bearer umai_live_eu_{CANARY}"}),
    )
    with patched(admission, "throttle", FailedAuthThrottle(1, 1e9, max_sources=100)):
        engine.post(url, json=evaluate_body(text), headers={"Authorization": "Bearer nope"})
        h.probe(
            "engine",
            "429 too_many_failed_attempts",
            post(evaluate_body(text), headers={"Authorization": "Bearer nope"}),
        )
    h.probe("engine", "403 tenant_mismatch", post(evaluate_body(text, tenant_id="someone-else")))
    h.probe(
        "engine", "403 insufficient_scope", post(evaluate_body(text), headers=h.bearer("gateway"))
    )
    outside = TestClient(engine.app, client=OUTSIDE, raise_server_exceptions=False)
    h.probe(
        "engine",
        "403 ip_not_allowed",
        lambda: outside.post(url, json=evaluate_body(text), headers=h.bearer("network")),
    )
    h.probe(
        "engine",
        "403 guardrail_mismatch",
        post(evaluate_body(text, bundle={"id": "another"}), headers=h.bearer("app")),
    )
    no_bundle = evaluate_body(text)
    del no_bundle["bundle"]
    h.probe("engine", "422 bundle_required", post(no_bundle))
    missing = evaluate_body(text)
    del missing["timestamp"]
    h.probe("engine", "422 missing field", post(missing))
    h.probe(
        "engine", "422 unknown field named by content", post({**evaluate_body(text), text: text})
    )
    h.probe(
        "engine",
        "422 wrong role",
        post(
            evaluate_body(
                text,
                content={
                    "messages": [{"role": text, "content": text}],
                    "focus": "LAST_USER_MESSAGE",
                },
            )
        ),
    )
    h.probe(
        "engine",
        "422 content of a wrong type",
        post(
            evaluate_body(
                text,
                content={
                    "messages": [{"role": "user", "content": {"x": text}}],
                    "focus": "LAST_USER_MESSAGE",
                },
            )
        ),
    )
    h.probe(
        "engine", "422 metadata keys and values", post(evaluate_body(text, metadata={text: [text]}))
    )
    h.probe("engine", "422 invalid JSON", post(raw=f'{{"content": "{text}'.encode()))
    h.probe("engine", "422 body not an object", post([text]))
    h.probe(
        "engine",
        "400 inline bundle refused",
        post(evaluate_body(text, bundle=None, bundle_inline={"id": text, "version": 1})),
    )
    h.probe("engine", "413 input_too_large", post(evaluate_body(big)))
    with patched(admission, "limiter", RateLimiter(0.001, 1)):
        engine.post(url, json=evaluate_body("Merhaba"), headers=full)
        h.probe("engine", "429 rate_limited", post(evaluate_body(text)))
    h.probe("engine", "429 quota_exceeded", post(evaluate_body(text), headers=h.bearer("quota")))
    h.probe(
        "engine",
        "200 failure decision: bundle not found",
        post(evaluate_body(text, bundle={"id": "no-such-guardrail"})),
    )
    h.probe(
        "engine",
        "200 failure decision: L1b provider not configured",
        post(evaluate_body(f"{CANARY} toplantı notları", bundle={"id": BUNDLE, "version": 5})),
    )
    detector = engine.app.state.evaluator._detector
    with patched(detector, "adetect_segments", _crash):
        h.probe("engine", "200 failure decision: L0 crashed", post(evaluate_body(text)))
    with patched(engine.app.state, "ready", False):
        h.probe("engine", "503 not ready", post(evaluate_body(text)))
    with patched(engine.app.state.evaluator, "evaluate", _crash):
        h.probe("engine", "500 evaluator crashed", post(evaluate_body(text)))


# -------------------------------------------------------------------- gateway


def gateway_probes(h: Harness, gateway: TestClient) -> None:
    text = h.values.text()
    masked = h.values.text(MASKED_TYPES)  # reaches the upstream, masked
    full = {**h.bearer("full"), "X-Umai-Application": "app"}
    admission = gateway.app.state.admission
    settings = gateway.app.state.settings

    def chat(
        content: Any = None, headers: dict | None = None, raw: bytes | None = None, **extra: Any
    ):
        def send() -> httpx.Response:
            if raw is not None:
                return gateway.post(
                    "/v1/chat/completions",
                    content=raw,
                    headers={**(headers or full), "content-type": "application/json"},
                )
            body = {"model": "m", "messages": [{"role": "user", "content": content}], **extra}
            return gateway.post(
                "/v1/chat/completions", json=body, headers=headers if headers is not None else full
            )

        return send

    h.probe("gateway", "401 no key", chat(text, headers={}))
    h.probe("gateway", "401 bad key", chat(text, headers={"Authorization": f"Bearer sk-{CANARY}"}))
    with patched(admission, "throttle", FailedAuthThrottle(1, 1e9, max_sources=100)):
        gateway.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": []},
            headers={"Authorization": "Bearer nope"},
        )
        h.probe(
            "gateway",
            "429 too_many_failed_attempts",
            chat(text, headers={"Authorization": "Bearer nope"}),
        )
    h.probe(
        "gateway",
        "403 insufficient_scope",
        chat(text, headers={**h.bearer("evaluate"), "X-Umai-Application": "app"}),
    )
    outside = TestClient(gateway.app, client=OUTSIDE, raise_server_exceptions=False)
    h.probe(
        "gateway",
        "403 ip_not_allowed",
        lambda: outside.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": text}]},
            headers={**h.bearer("network"), "X-Umai-Application": "app"},
        ),
    )
    h.probe(
        "gateway",
        "403 application_mismatch",
        chat(text, headers={**h.bearer("app"), "X-Umai-Application": "other"}),
    )
    h.probe("gateway", "400 application_required", chat(text, headers=h.bearer("full")))
    h.probe(
        "gateway", "400 invalid JSON", chat(raw=f'{{"messages": [{{"content": "{text}'.encode())
    )
    h.probe(
        "gateway",
        "400 body not an object",
        lambda: gateway.post("/v1/chat/completions", json=[text], headers=full),
    )
    h.probe(
        "gateway",
        "400 model missing",
        lambda: gateway.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": text}]},
            headers=full,
        ),
    )
    h.probe(
        "gateway",
        "400 malformed message",
        lambda: gateway.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": text, "content": text}]},
            headers=full,
        ),
    )
    h.probe("gateway", "400 content of a wrong type", chat({"text": text}))
    h.probe("gateway", "400 provider_key_required", chat(text, model="@pt/gpt"))
    h.probe("gateway", "guardrail refusal (blocked input)", chat(text))
    with patched(settings, "gateway_max_input_chars", 1_000):
        h.probe("gateway", "413 input too large", chat(text * 2))
    with patched(settings, "gateway_max_request_bytes", 10_000):
        h.probe("gateway", "413 request_too_large", chat(text * 20))
    with patched(admission, "limiter", RateLimiter(0.001, 1)):
        gateway.post("/v1/chat/completions", json={"model": "m", "messages": []}, headers=full)
        h.probe("gateway", "429 rate_limited", chat(text))
    h.probe(
        "gateway",
        "429 quota_exceeded",
        chat(text, headers={**h.bearer("quota"), "X-Umai-Application": "app"}),
    )
    for mode, label in (
        ("timeout", "504 upstream timeout"),
        ("connect", "502 upstream unreachable"),
        ("garbage", "502 upstream answer not JSON"),
    ):
        with patched(h.upstream, "mode", mode):
            h.probe("gateway", label, chat(masked))
    for mode, label in (
        ("echo400", "upstream 400 echoing its input"),
        ("echo500", "upstream 500 echoing its input"),
    ):
        with patched(h.upstream, "mode", mode):
            h.probe("gateway", f"{label} (passed through)", chat(masked), passthrough=True)
    blocked_answer = f"{CANARY} " + " ".join(h.values.by_type.get("TR_TCKN", ["10000000146"]))
    with patched(h.upstream, "answer", blocked_answer):
        h.probe("gateway", "guardrail refusal (blocked answer)", chat(masked))
        h.probe("gateway", "guardrail refusal (blocked answer, stream)", chat(masked, stream=True))
    with patched(h.upstream, "mode", "stream_error"):  # passes through (gateway-api, Streaming)
        h.probe(
            "gateway",
            "upstream error event mid-stream (passed through)",
            chat(masked, stream=True),
            passthrough=True,
        )
    responses = {"model": "m", "input": text}
    h.probe(
        "gateway",
        "responses: guardrail refusal",
        lambda: gateway.post("/v1/responses", json=responses, headers=full),
    )
    h.probe(
        "gateway",
        "responses: 400 malformed input",
        lambda: gateway.post(
            "/v1/responses",
            json={"model": "m", "input": [{"role": text, "content": 7}]},
            headers=full,
        ),
    )
    with patched(h.upstream, "mode", "timeout"):
        h.probe(
            "gateway",
            "responses: 504 upstream timeout",
            lambda: gateway.post(
                "/v1/responses", json={"model": "m", "input": masked}, headers=full
            ),
        )
    h.probe(
        "gateway",
        "embeddings: guardrail refusal",
        lambda: gateway.post("/v1/embeddings", json={"model": "m", "input": [text]}, headers=full),
    )
    h.probe(
        "gateway",
        "embeddings: 400 malformed input",
        lambda: gateway.post(
            "/v1/embeddings", json={"model": "m", "input": {"x": text}}, headers=full
        ),
    )
    with patched(h.upstream, "mode", "connect"):
        h.probe(
            "gateway",
            "embeddings: 502 upstream unreachable",
            lambda: gateway.post(
                "/v1/embeddings", json={"model": "m", "input": [masked]}, headers=full
            ),
        )
    with patched(gateway.app.state.guard, "check", _crash):
        h.probe("gateway", "500 guardrail crashed", chat(text))


# -------------------------------------------------------------------- policy service


def _fill(schema: dict[str, Any], components: dict[str, Any], text: str) -> Any:
    """A body for a request schema with every property set to content (wrong types included:
    a string where a list or a number is expected is what we want refused), plus a key named by
    content."""

    if "$ref" in schema:
        schema = components.get(schema["$ref"].rsplit("/", 1)[-1], {})
    body = {name: text for name in schema.get("properties", {})}
    body[text] = text
    return body


def policy_probes(h: Harness, control: TestClient) -> None:
    text = h.values.text()
    openapi = control.app.openapi()
    components = openapi.get("components", {}).get("schemas", {})
    unauthenticated = TestClient(control.app, raise_server_exceptions=False)
    for path, methods in sorted(openapi["paths"].items()):
        concrete = _PATH_PARAM.sub(
            lambda m: TENANT if "tenant" in m.group(1) or m.group(1) == "id" else f"x-{m.group(1)}",
            path,
        )
        for method, operation in sorted(methods.items()):
            if method not in ("post", "put", "patch", "get", "delete"):
                continue
            label = f"{method.upper()} {path}"
            query = [p["name"] for p in operation.get("parameters", []) if p.get("in") == "query"]
            body_schema = (
                operation.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema")
            )
            if query:
                params = {name: text for name in query}
                h.probe(
                    "policy",
                    f"{label}: query parameters",
                    lambda m=method, c=concrete, p=params: control.request(m.upper(), c, params=p),
                )
            if body_schema is None:
                continue
            h.probe(
                "policy",
                f"{label}: no credential",
                lambda m=method, c=concrete: unauthenticated.request(
                    m.upper(), c, json={text: text}
                ),
            )
            h.probe(
                "policy",
                f"{label}: invalid JSON",
                lambda m=method, c=concrete: control.request(
                    m.upper(),
                    c,
                    content=f'{{"x": "{text}'.encode(),
                    headers={"content-type": "application/json"},
                ),
            )
            h.probe(
                "policy",
                f"{label}: body not an object",
                lambda m=method, c=concrete: control.request(m.upper(), c, json=[text]),
            )
            h.probe(
                "policy",
                f"{label}: fields set to content",
                lambda m=method, c=concrete, s=body_schema: control.request(
                    m.upper(), c, json=_fill(s, components, text)
                ),
            )
    with patched(control.app.state.keys, "list", _crash):
        h.probe(
            "policy",
            "500 repository crashed (GET keys)",
            lambda: control.get(f"/v1/tenants/{TENANT}/keys"),
        )


# -------------------------------------------------------------------- run


def run_error_paths(cases: list[dict], detector: EntityDetector | None = None) -> list[Probe]:
    """Every probe, against fresh apps in a temporary directory; ``detector`` is shared when
    given (tests), else made and closed here."""

    values = Values.from_cases(cases)
    work = Path(tempfile.mkdtemp(prefix="umai-leak-errors-"))
    own = detector is None
    if detector is None:
        detector = EntityDetector(lambda tenant: f"eval-{tenant}".encode(), threads=1)
        detector.warm_up()
    harness = Harness(values, detector, work)
    harness.prepare()
    root, engine_logger = logging.getLogger(), logging.getLogger("umai")
    previous = (root.level, engine_logger.level)
    root.addHandler(harness.capture)
    root.setLevel(logging.DEBUG)
    engine_logger.setLevel(logging.DEBUG)
    try:
        with harness.apps() as (control, engine, gateway):
            harness.mint(control, engine, gateway)
            engine_probes(harness, engine)
            gateway_probes(harness, gateway)
            policy_probes(harness, control)
    finally:
        root.removeHandler(harness.capture)
        root.setLevel(previous[0])
        engine_logger.setLevel(previous[1])
        if own:
            detector.close()
        shutil.rmtree(work, ignore_errors=True)
    return harness.probes


def report_lines(probes: list[Probe]) -> list[str]:
    lines = [
        "## Error paths",
        "",
        "Every probe carries synthetic raw values of every dataset type and a canary, and is "
        "refused or fails on purpose (see `evals/suites/leak_errors.py`).",
        "",
        "| Surface | Probes | 4xx | 5xx | Other | Answers with a raw value | "
        "Logs with a raw value | Caller content echoed by UMAI | Must be |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for surface, name in (
        ("engine", "Engine API"),
        ("gateway", "Gateway"),
        ("policy", "Policy service"),
    ):
        own = [p for p in probes if p.surface == surface]
        mine = [p for p in own if not p.passthrough]
        lines.append(
            f"| {name} | {len(own)} | {sum(400 <= p.status < 500 for p in own)} | "
            f"{sum(p.status >= 500 for p in own)} | {sum(p.status < 400 for p in own)} | "
            f"{sum(bool(p.response) for p in mine)} | {sum(bool(p.logs) for p in own)} | "
            f"{sum(p.echo for p in mine) + sum(p.echo_logs for p in own)} | 0 |"
        )
    passed = [p for p in probes if p.passthrough]
    missed = sorted({t for p in passed for t in p.response})
    policy = [p for p in probes if p.surface == "policy"]
    lines += [
        "",
        "Provider errors the gateway passes through by contract (an error body with "
        f"`X-Umai-Upstream-Error`, or an error event mid-stream): {len(passed)} probes. The "
        "scripted provider echoes what it received: masked content"
        + (
            "; raw values in the echo, which L0 left unmasked and the provider therefore "
            f"received (L0 misses, not error-path leaks): {', '.join(missed)}."
            if missed
            else "; no raw value."
        ),
        "",
        "Error codes covered: "
        + ", ".join(
            sorted({f"{p.surface} {p.status} {p.code}" for p in probes if p.surface != "policy"})
        )
        + ".",
        "",
        "Policy service answers: "
        + ", ".join(
            f"HTTP {status} × {count}"
            for status, count in sorted(Counter(p.status for p in policy).items())
        )
        + " over "
        + str(len({p.label.split(":")[0] for p in policy}))
        + " routes.",
        "",
    ]
    leaks = [p for p in probes if p.leaked]
    if leaks:
        lines += ["Leaking probes:", ""]
        for p in leaks:
            where = []
            if p.response:
                where.append(f"answer: {', '.join(p.response)}")
            if p.logs:
                where.append(f"logs: {', '.join(p.logs)}")
            if p.echo and not p.passthrough:
                where.append("answer echoes caller content")
            if p.echo_logs:
                where.append("logs echo caller content")
            lines.append(
                f"- {p.surface} `{p.label}` → HTTP {p.status} ({p.code}): {'; '.join(where)}"
            )
        lines.append("")
    return lines
