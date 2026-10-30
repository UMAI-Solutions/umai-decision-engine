"""Gateway records (ADR-0041 §4, usage 0.2): one usage record per call whatever its exit, one
decision event per check, and a stream's window re-checks collapsed into its final check (or
the re-check that stopped it). In memory, fake upstream; synthetic data only."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.integration.test_gateway import (
    CARD,
    GUARDRAIL,
    FakeUpstream,
    app_yaml,
    ask,
    completion,
    stream_events,
    text_stream,
)
from umai_decision_engine.contracts.events import DecisionEvent, UsageRecord
from umai_decision_engine.events.recorder import MemoryRecorder
from umai_decision_engine.gateway.app import create_gateway_app
from umai_decision_engine.settings import Settings


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()


@pytest.fixture
def make(write_bundle, detector, tmp_path, upstream):
    clients: list[TestClient] = []

    def _make(*, applications: str | None = None, **settings: Any):
        write_bundle(GUARDRAIL.format(mode="ENFORCE"), bundle_id="gr-gw", active=1)
        applications_dir = tmp_path / "applications"
        applications_dir.mkdir(exist_ok=True)
        (applications_dir / "tenant-a.yaml").write_text(
            applications or app_yaml(), encoding="utf-8"
        )
        cfg = Settings(
            env="development",
            policy_dir=write_bundle.root,
            bundle_poll_interval_s=0,
            l0_threads=1,
            default_tenant_id="tenant-a",
            applications_dir=applications_dir,
            applications_poll_interval_s=0,
            **settings,
        )
        recorder = MemoryRecorder()
        app = create_gateway_app(
            cfg,
            detector=detector,
            upstream_transport=httpx.MockTransport(upstream.handler),
            recorder=recorder,
        )
        client = TestClient(app, headers={"X-Umai-Provider-Key": "sk-test"})
        client.__enter__()
        clients.append(client)
        return client, recorder

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


def events(recorder: MemoryRecorder) -> list[DecisionEvent]:
    return [r for r in recorder.records if isinstance(r, DecisionEvent)]


def meters(recorder: MemoryRecorder, source: str = "gateway") -> dict[str, int]:
    out: dict[str, int] = {}
    for r in recorder.records:
        if isinstance(r, UsageRecord) and r.source == source:
            out[r.meter] = out.get(r.meter, 0) + r.quantity
    return out


def calls(recorder: MemoryRecorder) -> list[UsageRecord]:
    return [
        r for r in recorder.records if isinstance(r, UsageRecord) and r.meter == "gateway_calls"
    ]


def test_a_call_has_one_usage_record_and_one_event_per_check(make) -> None:
    client, recorder = make()
    assert ask(client, "Merhaba").status_code == 200

    [call] = calls(recorder)
    assert call.event_id.startswith("gc-") and call.application_id == "app-eu"
    assert (call.model, call.bundle_id) == ("gpt-x", "gr-gw") and call.provider
    assert [(e.phase, e.source, e.gateway_call_id) for e in events(recorder)] == [
        ("PRE_LLM", "gateway", call.event_id),
        ("POST_LLM", "gateway", call.event_id),
    ]
    assert meters(recorder) == {
        "evaluations": 2,
        "gateway_calls": 1,
        "provider_input_tokens": 11,
        "provider_output_tokens": 7,
    }


def test_a_refused_input_is_one_call_without_tokens(make, upstream) -> None:
    client, recorder = make()
    ask(client, f"Kartım {CARD}")
    assert upstream.requests == []  # never forwarded
    assert meters(recorder) == {"evaluations": 1, "gateway_calls": 1}
    [event] = events(recorder)
    assert (event.phase, event.decision.action) == ("PRE_LLM", "BLOCK")


def test_an_output_refusal_raised_as_an_error_is_still_one_call(make, upstream) -> None:
    client, recorder = make(applications=app_yaml(refusal="error"))
    upstream.reply = lambda body: completion(f"Kart {CARD}")
    assert ask(client, "Bir kart yaz").status_code == 400
    assert len(calls(recorder)) == 1  # closed by the middleware, once
    assert [e.decision.action for e in events(recorder)] == ["ALLOW", "BLOCK"]


def test_an_upstream_failure_is_still_one_call(make, upstream) -> None:
    client, recorder = make()
    upstream.error = httpx.ConnectError("upstream down")
    response = ask(client, "Merhaba")
    assert response.status_code >= 500
    assert len(calls(recorder)) == 1
    assert meters(recorder)["gateway_calls"] == 1


def test_a_streams_window_rechecks_collapse_into_its_final_check(make, upstream) -> None:
    client, recorder = make(gateway_stream_holdback_chars=32, gateway_stream_step_chars=8)
    upstream.stream = text_stream(*(["Bu uzun bir cevaptır ve parça parça akar. "] * 6))
    stream_events(ask(client, "Uzun yaz", stream=True))

    phases = [e.phase for e in events(recorder)]
    assert phases == ["PRE_LLM", "POST_LLM"]  # many window checks ran; one answer record
    assert meters(recorder) == {
        "evaluations": 2,
        "gateway_calls": 1,
        "provider_input_tokens": 5,
        "provider_output_tokens": 3,
    }


def test_a_stream_stopped_by_a_recheck_records_that_check(make, upstream) -> None:
    client, recorder = make(gateway_stream_holdback_chars=32, gateway_stream_step_chars=8)
    upstream.stream = text_stream(
        "Önce zararsız bir cümle yazıyorum. ", f"Kart {CARD} ", "bitti.", "Ve devam."
    )
    stream_events(ask(client, "Bir kart yaz", stream=True))

    answers = [e for e in events(recorder) if e.phase == "POST_LLM"]
    assert [e.decision.action for e in answers] == ["BLOCK"]  # the deciding re-check only
    assert len(calls(recorder)) == 1
