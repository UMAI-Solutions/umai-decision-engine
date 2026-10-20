"""L0 entity detector: normalize → analyze → validate → context → resolve → hash → redact.

Presidio's ``analyze()`` is synchronous and CPU-bound and its thread-safety is
undocumented, so every worker thread owns its own ``AnalyzerEngine`` and the async API
runs detection in a dedicated thread pool, never on the event loop.

Text made of several segments (messages, attachment parts) is scanned one segment at a time
and the spans are reported in ``SEPARATOR.join(segments)``; with a cache, a segment seen
before is not scanned again (ADR-0037).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider

from umai_decision_engine.contracts.bundle import L0Config
from umai_decision_engine.contracts.entities import (
    DetectedEntity,
    EntityDetectionResult,
    EntitySummary,
)
from umai_decision_engine.l0.cache import DetectionCache
from umai_decision_engine.l0.context import has_context
from umai_decision_engine.l0.hashing import KeyProvider, value_hash
from umai_decision_engine.l0.normalize import normalize
from umai_decision_engine.l0.recognizers.base import META_VALIDATED, SpecRecognizer
from umai_decision_engine.l0.recognizers.catalog import (
    ALL_SPECS,
    SPECS_BY_ENTITY,
    SPECS_BY_RECOGNIZER,
)
from umai_decision_engine.l0.recognizers.custom import custom_recognizers
from umai_decision_engine.l0.recognizers.network import registrable_domain, url_domain
from umai_decision_engine.l0.redact import PlaceholderAllocator, apply_redaction
from umai_decision_engine.l0.target import SEPARATOR

logger = logging.getLogger("umai.de.l0")

LANGUAGES: tuple[str, ...] = ("tr", "en")
_TURKISH_LETTERS = frozenset("ğĞşŞıİçÇöÖüÜ")
_CHUNK_OVERLAP = 256

# Presidio logs context words at DEBUG and every recognizer load at INFO; keep WARNING+.
logging.getLogger("presidio-analyzer").setLevel(logging.WARNING)


def build_analyzer() -> AnalyzerEngine:
    """Minimal registry of our recognizers, registered for every language; no spaCy pipeline."""

    nlp_engine = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "no_op",
            "models": [{"lang_code": lang, "model_name": "no_op"} for lang in LANGUAGES],
        }
    ).create_engine()
    registry = RecognizerRegistry(supported_languages=list(LANGUAGES))
    for lang in LANGUAGES:
        for spec in ALL_SPECS:
            registry.add_recognizer(SpecRecognizer(spec, supported_language=lang))
    return AnalyzerEngine(
        registry=registry,
        nlp_engine=nlp_engine,
        supported_languages=list(LANGUAGES),
        log_decision_process=False,
    )


def detect_language(text: str, hint: str | None) -> str:
    if hint:
        code = hint.lower()[:2]
        if code in LANGUAGES:
            return code
    return "tr" if any(ch in _TURKISH_LETTERS for ch in text) else "en"


@dataclass(frozen=True)
class _Hit:
    type: str
    start: int
    end: int
    score: float
    validated: bool | None
    recognizer: str
    priority: int


@dataclass(frozen=True)
class _Found:
    """An accepted entity of one segment, in the segment's original coordinates."""

    type: str
    start: int
    end: int
    score: float
    recognizer: str
    validated: bool | None
    value_hash: str


@dataclass(frozen=True)
class _Scan:
    """What scanning one segment produced: no text, no values (ADR-0037)."""

    entities: tuple[_Found, ...]
    normalized_length: int


def _chunks(text: str, size: int) -> list[tuple[int, str]]:
    if len(text) <= size:
        return [(0, text)]
    step = size - _CHUNK_OVERLAP
    return [(i, text[i : i + size]) for i in range(0, len(text), step)]


def _resolve_overlaps(hits: list[_Hit]) -> list[_Hit]:
    """Prefer checksum-validated, then more specific type, then longer, then higher score."""

    ordered = sorted(
        hits,
        key=lambda h: (h.validated is not True, -h.priority, -(h.end - h.start), -h.score, h.start),
    )
    accepted: list[_Hit] = []
    for hit in ordered:
        if all(hit.end <= a.start or hit.start >= a.end for a in accepted):
            accepted.append(hit)
    return sorted(accepted, key=lambda h: h.start)


class EntityDetector:
    def __init__(
        self, key_provider: KeyProvider, *, threads: int = 4, cache_entries: int = 0
    ) -> None:
        self._key_provider = key_provider
        self._local = threading.local()
        self._executor = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="umai-l0")
        self._threads = threads
        self.cache: DetectionCache[_Scan] | None = (
            DetectionCache(cache_entries) if cache_entries > 0 else None
        )

    # ----------------------------------------------------------- lifecycle

    def _engine(self) -> AnalyzerEngine:
        engine = getattr(self._local, "engine", None)
        if engine is None:
            engine = build_analyzer()
            self._local.engine = engine
        return engine

    def warm_up(self) -> None:
        """Build one engine per worker thread and run a synthetic sample through each."""

        config = L0Config(entities=sorted(SPECS_BY_ENTITY))
        sample = (
            "TC kimlik 10000000146, IBAN TR33 0006 1005 1978 6457 8413 26, DE89370400440532013000, "
            "cep 0532 123 45 67, kart 4242 4242 4242 4242, test@example.com, call +1 212 555 0147, "
            "tel 06 39 98 12 34, IP 85.105.12.34, https://example-unknown.xyz/a, SSN 234-56-7890, "
            "vergi no 4540536920, plaka 34 ABC 123, DNI 12345678Z, Ahmet Yılmaz, "
            "Moda Cad. No: 12 Kadıköy/İstanbul, password=Example123"
        )  # touches every recognizer, incl. phonenumbers metadata (lazy-loaded)
        barrier = threading.Barrier(self._threads)

        def _warm() -> None:
            barrier.wait(timeout=30)
            self._scan(sample, key=b"warm-up", config=config, language="tr")

        futures = [self._executor.submit(_warm) for _ in range(self._threads)]
        for future in futures:
            future.result(timeout=60)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ----------------------------------------------------------- detection

    def detect(
        self, text: str, *, tenant_id: str, config: L0Config, language_hint: str | None = None
    ) -> EntityDetectionResult:
        key = self._key_provider(tenant_id)
        return self._detect([text], key=key, config=config, language_hint=language_hint)

    async def adetect(
        self, text: str, *, tenant_id: str, config: L0Config, language_hint: str | None = None
    ) -> EntityDetectionResult:
        return await self.adetect_segments(
            [text], tenant_id=tenant_id, config=config, language_hint=language_hint
        )

    async def adetect_segments(
        self,
        segments: list[str],
        *,
        tenant_id: str,
        config: L0Config,
        language_hint: str | None = None,
        cache: bool = True,
    ) -> EntityDetectionResult:
        """Detect in ``SEPARATOR.join(segments)``, scanning each segment on its own. ``cache``
        False keeps one-off texts (answer checks while streaming) out of the cache."""

        key = self._key_provider(tenant_id)
        loop = asyncio.get_running_loop()
        call = functools.partial(
            self._detect,
            segments,
            key=key,
            config=config,
            language_hint=language_hint,
            use_cache=cache,
        )
        return await loop.run_in_executor(self._executor, call)

    def _detect(
        self,
        segments: list[str],
        *,
        key: bytes,
        config: L0Config,
        language_hint: str | None,
        use_cache: bool = True,
    ) -> EntityDetectionResult:
        started = time.perf_counter()
        text = SEPARATOR.join(segments)
        language = detect_language(text, language_hint)
        cache = self.cache if use_cache else None
        scope = cache.scope(key, config, language) if cache is not None else b""
        scans: list[_Scan] = []
        for segment in segments:
            scan = cache.get(scope, segment) if cache is not None else None
            if scan is None:
                scan = self._scan(segment, key=key, config=config, language=language)
                if cache is not None:
                    cache.put(scope, segment, scan)
            scans.append(scan)

        allocator = PlaceholderAllocator()
        detected: list[DetectedEntity] = []
        offset = 0
        for segment, scan in zip(segments, scans, strict=True):
            for found in scan.entities:
                detected.append(
                    DetectedEntity(
                        type=found.type,
                        start=found.start + offset,
                        end=found.end + offset,
                        score=found.score,
                        recognizer=found.recognizer,
                        validated=found.validated,
                        value_hash=found.value_hash,
                        placeholder=allocator.placeholder(found.type, found.value_hash),
                    )
                )
            offset += len(segment) + len(SEPARATOR)

        summary: dict[str, tuple[int, set[str]]] = {}
        for entity in detected:
            count, hashes = summary.get(entity.type, (0, set()))
            hashes.add(entity.value_hash)
            summary[entity.type] = (count + 1, hashes)

        normalized = sum(s.normalized_length for s in scans) + len(SEPARATOR) * max(
            len(scans) - 1, 0
        )
        return EntityDetectionResult(
            entities=detected,
            summary=[
                EntitySummary(type=t, count=c, distinct=len(h)) for t, (c, h) in summary.items()
            ],
            redacted_text=apply_redaction(
                text, [(e.start, e.end, e.placeholder) for e in detected]
            ),
            language=language,
            truncated=normalized > config.max_chars,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )

    def _scan(self, text: str, *, key: bytes, config: L0Config, language: str) -> _Scan:
        norm = normalize(text)
        engine = self._engine()
        entities = [e for e in config.entities if e in SPECS_BY_ENTITY]
        custom = custom_recognizers(config.custom_recognizers, language)
        specs = {**SPECS_BY_ENTITY, **{c.spec.entity: c.spec for c in custom}}
        by_recognizer = {**SPECS_BY_RECOGNIZER, **{c.name: c.spec for c in custom}}
        allowed_domains = {registrable_domain(d) for d in config.allowed_domains}

        raw: dict[tuple[str, int, int], _Hit] = {}
        for offset, chunk in _chunks(norm.text, config.max_chars):
            results = (
                engine.analyze(
                    text=chunk, language=language, entities=entities, score_threshold=0.0
                )
                if entities
                else []
            )
            for recognizer in custom:
                results.extend(recognizer.analyze(chunk, [recognizer.spec.entity]))
            for r in results:
                spec = by_recognizer.get(
                    r.recognition_metadata.get(RecognizerResult.RECOGNIZER_NAME_KEY, ""),
                    specs[r.entity_type],
                )
                start, end = r.start + offset, r.end + offset
                score = r.score
                if has_context(norm.text, start, end, spec.context):
                    score = min(1.0, score + spec.context_boost)
                if score < config.min_score_overrides.get(r.entity_type, config.min_score):
                    continue
                if (
                    r.entity_type == "UNKNOWN_LINK"
                    and allowed_domains
                    and url_domain(norm.text[start:end]) in allowed_domains
                ):
                    continue  # the guardrail trusts this domain (l0.allowed_domains)
                hit = _Hit(
                    type=r.entity_type,
                    start=start,
                    end=end,
                    score=round(score, 4),
                    validated=r.recognition_metadata.get(META_VALIDATED),
                    recognizer=r.recognition_metadata.get(
                        RecognizerResult.RECOGNIZER_NAME_KEY, spec.entity
                    ),
                    priority=spec.priority,
                )
                k = (hit.type, hit.start, hit.end)
                if k not in raw or raw[k].score < hit.score:
                    raw[k] = hit

        found: list[_Found] = []
        for hit in _resolve_overlaps(list(raw.values())):
            spec = specs[hit.type]
            canonical = spec.canonical(norm.text[hit.start : hit.end])
            o_start, o_end = norm.to_original(hit.start, hit.end)
            found.append(
                _Found(
                    type=hit.type,
                    start=o_start,
                    end=o_end,
                    score=hit.score,
                    recognizer=hit.recognizer,
                    validated=hit.validated,
                    value_hash=value_hash(key, hit.type, canonical),
                )
            )
        return _Scan(entities=tuple(found), normalized_length=len(norm.text))
