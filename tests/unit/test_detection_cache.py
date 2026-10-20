"""Per-segment scanning and the L0 detection cache (ADR-0037), on synthetic data."""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import CORE
from umai_decision_engine.contracts.bundle import L0Config
from umai_decision_engine.l0.cache import DetectionCache
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.l0.target import SEPARATOR

HISTORY = [
    "Merhaba, TC kimlik numaram 10000000146.",
    "Teşekkürler, kaydınızı buldum. Size mail a@b.co adresinden ulaşacağız.",
    "Eşimin numarası 17291716060, onu da ekler misiniz? Yine a@b.co adresine yazın.",
]


@pytest.fixture(scope="module")
def cached() -> EntityDetector:
    d = EntityDetector(lambda tenant: f"key-{tenant}".encode(), threads=1, cache_entries=100)
    yield d
    d.close()


def _segments(detector: EntityDetector, segments: list[str], config: L0Config, tenant="t1"):
    return asyncio.run(detector.adetect_segments(segments, tenant_id=tenant, config=config))


def _comparable(result) -> dict:
    return result.model_dump(exclude={"latency_ms"})


def test_one_segment_is_the_same_as_one_text(detector, core_config) -> None:
    text = HISTORY[2]
    assert _comparable(_segments(detector, [text], core_config)) == _comparable(
        detector.detect(text, tenant_id="t1", config=core_config)
    )


def test_spans_and_placeholders_refer_to_the_joined_text(detector, core_config) -> None:
    joined = SEPARATOR.join(HISTORY)
    result = _segments(detector, HISTORY, core_config)
    found = [(e.type, joined[e.start : e.end]) for e in result.entities]
    assert found == [
        ("TR_TCKN", "10000000146"),
        ("EMAIL_ADDRESS", "a@b.co"),
        ("TR_TCKN", "17291716060"),
        ("EMAIL_ADDRESS", "a@b.co"),
    ]
    # numbered across the whole history, the same value keeps its placeholder
    assert [e.placeholder for e in result.entities] == [
        "<TR_TCKN_1>",
        "<EMAIL_ADDRESS_1>",
        "<TR_TCKN_2>",
        "<EMAIL_ADDRESS_1>",
    ]
    assert "10000000146" not in result.redacted_text
    assert result.redacted_text.count(SEPARATOR) == len(HISTORY) - 1
    # no entity crosses a boundary, so the joined text gives the same answer
    assert _comparable(result) == _comparable(
        detector.detect(joined, tenant_id="t1", config=core_config)
    )


def test_a_resent_history_is_not_scanned_again(cached, core_config) -> None:
    first = _segments(cached, HISTORY[:2], core_config)
    hits = cached.cache.hits
    second = _segments(cached, HISTORY, core_config)  # the next turn: one new message
    assert cached.cache.hits == hits + 2
    assert _comparable(second)["entities"][:2] == _comparable(first)["entities"]
    assert _comparable(second) == _comparable(_segments(cached, HISTORY, core_config))


def test_cache_never_mixes_tenants_or_configurations(cached, core_config) -> None:
    a = _segments(cached, [HISTORY[0]], core_config, tenant="tenant-a")
    hits = cached.cache.hits
    b = _segments(cached, [HISTORY[0]], core_config, tenant="tenant-b")
    assert cached.cache.hits == hits  # another tenant's hashing key: a miss
    assert a.entities[0].value_hash != b.entities[0].value_hash
    stricter = L0Config(entities=CORE, min_score=0.99)
    _segments(cached, [HISTORY[0]], stricter, tenant="tenant-a")
    assert cached.cache.hits == hits  # another configuration: a miss


def test_cache_holds_no_text_or_values(cached, core_config) -> None:
    _segments(cached, HISTORY, core_config)
    stored = repr(list(cached.cache._entries.items()))
    for raw in ("10000000146", "17291716060", "a@b.co", "Merhaba", "kimlik"):
        assert raw not in stored


def test_cache_is_bounded() -> None:
    cache: DetectionCache[int] = DetectionCache(2)
    scope = b"scope"
    for i, text in enumerate(("one", "two", "three")):
        cache.put(scope, text, i)
    assert len(cache) == 2
    assert cache.get(scope, "one") is None
    assert cache.get(scope, "three") == 2
    with pytest.raises(ValueError):
        DetectionCache(0)
