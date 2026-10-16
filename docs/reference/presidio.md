# Presidio

**Last verified:** 2026-10-15 (source read at tag `2.2.364`; not executed end to end) ·
**Used by:** L0 · Labels: see [README](README.md)

## Project status

- **New home:** `microsoft/presidio` now redirects to **`data-privacy-stack/presidio`**, a
  community-governed project, still MIT. Docs: https://presidio.dataprivacystack.org/.
  Images moved to `ghcr.io/data-privacy-stack/presidio-*`; MCR images are no longer updated
  ([transition note](https://github.com/data-privacy-stack/presidio/blob/main/docs/project_transition.md)).
- **Latest:** `presidio-analyzer` / `presidio-anonymizer` **2.2.364** (2026-07-22), Python
  3.10–3.14, MIT. Previous: 2.2.363 (2026-06-28), 2.2.362 (2026-03-15).
- Analyzer deps: `spacy>=3.4.4,!=3.7.0,<4`, `regex`, `phonenumbers>=9.0.28`,
  `pydantic>=2.12.5`, `tldextract`, `pyyaml`. Extras: `transformers`, `stanza`, `gliner`,
  `langextract`, `azure-ai-language`, `ahds`.
- **Pin exact versions.** Breaking changes happen in patch releases (e.g. random hash salt
  in 2.2.361; config consolidation in 2.2.363).

## Key APIs (2.2.364)

| API | Facts that matter to us |
|---|---|
| `AnalyzerEngine(registry, nlp_engine, log_decision_process, default_score_threshold=0, supported_languages, context_aware_enhancer)` | Always runs `nlp_engine.process_text()` unless `nlp_artifacts` passed. **Passing `entities=[...]` does not skip the NLP pipeline.** Recognizers lazy-load on first `analyze` → warm up. Default `AnalyzerEngine()` loads `en_core_web_lg` (400 MB) — never use the default |
| `RecognizerRegistry(supported_languages, global_regex_flags=DOTALL\|MULTILINE\|IGNORECASE)` | Language gating is exact: a recognizer runs only for its `supported_language`. Build a minimal registry instead of `load_predefined_recognizers()`. `countries=[...]` filter since 2.2.363 |
| `PatternRecognizer(..., patterns, context, global_regex_flags)` | Uses `regex` with per-match timeout `REGEX_TIMEOUT_SECONDS` (int, default 60 — set `1`; sub-second values crash). **`validate_result`: `True` → score 1.0, `False` → dropped, `None` → keep pattern score.** `invalidate_result`: `True` → dropped |
| `LemmaContextAwareEnhancer` | `context_similarity_factor=0.35`, `min_score_with_context_similarity=0.4`, `context_prefix_count=5`, `context_suffix_count=0`, `context_matching_mode` `substring`/`whole_word`. Compares context words with **single lemmas** of the 5 preceding tokens |
| NLP engines | `spacy` (auto-downloads missing models!), `transformers`, `stanza`, `slim` (2.2.363: tokens + lemmas only, NER off), **`no_op`** (2.2.364: no tokens, so no text context; only `analyze(context=…)` works) |
| `HuggingFaceNerRecognizer` (2.2.362) | HF token-classification without spaCy alignment; docstring targets agglutinative languages **including Turkish**; `model_name`, `label_mapping`, `threshold=0.3`, `aggregation_strategy`, `chunk_size=400`, `chunk_overlap=40`, `device` |
| `GLiNERRecognizer` | Default `urchade/gliner_multi_pii-v1` (no Turkish in its card); `load_onnx_model=True` supported |
| `BatchAnalyzerEngine` | `analyze_iterator(texts, batch_size, n_process)` — for evals/bulk, not the request path |
| `AnonymizerEngine` operators | `replace`, `redact`, `mask`, `hash` (`sha256(text+salt)`, **random 32-byte salt per entity by default** since 2.2.361), `encrypt` (**AES-CBC, no MAC**), `keep`, `custom` |
| LLM recognizers (LangExtract, Azure) | Send text off-box; experimental — **not allowed in L0** |

## Turkish support in Presidio

All shipped **disabled by default** (2.2.363+):

| Presidio entity | Recognizer | Problems for us |
|---|---|---|
| `TR_NATIONAL_ID` | `TrNationalIdRecognizer`: `\b[1-9][0-9]{10}\b`, score 0.3, checksum in `validate_result` (→ 1.0) | `\b` misses `10000000146dır`, `tckn10000000146`, `x_10000000146`; multi-word context entries never match |
| `TR_LICENSE_PLATE` | `TrLicensePlateRecognizer` | Case-insensitive flags make it match `12 eki 2024`, `10 kg 500` with score 1.0; misses 5-digit plates |
| `TR_PHONE_NUMBER` | `PhoneRecognizer(supported_regions=["TR"])` | Default `PhoneRecognizer` regions exclude TR |
| `IBAN_CODE` | `IbanRecognizer` includes a TR pattern + mod-97 | OK |

Mapping to our entity names: `TR_NATIONAL_ID` → `TR_TCKN`, `TR_LICENSE_PLATE` → `TR_PLATE`,
`TR_PHONE_NUMBER` → `TR_PHONE`, `IBAN_CODE` (TR) → `TR_IBAN`; upstream `TR_TAX_ID` (PR #2250)
→ `TR_VKN`.

Not shipped: VKN ([PR #2250](https://github.com/data-privacy-stack/presidio/pull/2250),
open), Turkish NLP config, Turkish context words for generic recognizers, Turkish
case-folding fix ([PR #2208](https://github.com/data-privacy-stack/presidio/pull/2208),
open). Context enhancement fails for agglutinative languages
([issue #2216](https://github.com/data-privacy-stack/presidio/issues/2216)).

## Snippets (signatures checked against 2.2.364; not executed)

Pattern-only analyzer for Turkish and English:

```python
from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider

LANGS = ["tr", "en"]
nlp_engine = NlpEngineProvider(
    nlp_configuration={
        "nlp_engine_name": "no_op",  # or "slim" with a blank Turkish pipeline for text context
        "models": [{"lang_code": l, "model_name": "no_op"} for l in LANGS],
    }
).create_engine()

registry = RecognizerRegistry(supported_languages=LANGS)
for l in LANGS:  # gating is exact → register per language
    for rec in build_umai_recognizers(language=l):  # our own TR + generic recognizers
        registry.add_recognizer(rec)

analyzer = AnalyzerEngine(registry=registry, nlp_engine=nlp_engine, supported_languages=LANGS)
```

Slim engine with Turkish lemmas (needs `spacy-lookups-data`, MIT; **untested**):

```python
nlp = spacy.blank("tr")
nlp.add_pipe("lemmatizer", config={"mode": "lookup"})
nlp.initialize()
nlp.to_disk(
    path
)  # then {"nlp_engine_name": "slim", "models": [{"lang_code": "tr", "model_name": path}]}
```

Checksum recognizer pattern (VKN — weak checksum, so success keeps the low score):

```python
class TrVknRecognizer(PatternRecognizer):
    PATTERNS = [Pattern("TR_VKN_10", r"(?<![0-9])[0-9]{10}(?![0-9])", 0.2)]  # [0-9], not \d

    def validate_result(self, text):  # False drops; None keeps 0.2 so context must lift it
        return None if vkn_is_valid(text) else False
```

## Performance

| Source | Figure |
|---|---|
| Maintainer (2023) | "< 10 ms per 1000-token request" with tuning (small model, fewer recognizers; `PhoneRecognizer` slowest) |
| arXiv 2606.29567 | 23.5 ms/query, all English recognizers + `en_core_web_lg`, M2 Pro |
| Hobby benchmark, `en_core_web_md` | p50 4.6 ms @126 chars · 13.0 @418 · 43.1 @1 513 · 168.6 @5 893 — **linear in length** |
| Transformers / Stanza engines on CPU | "several minutes" vs spaCy "almost instantly" for bulk (maintainer) |

None measured on UMAI hardware. Implications: pattern-only path via `no_op`/`slim`; cap or
chunk input; NER only when needed; `analyze()` is synchronous and CPU-bound → never on the
event loop; thread-safety of `AnalyzerEngine` is **unverified** (prefer a process pool or
one engine per worker thread until tested).

## Production pitfalls (checklist)

1. **Leaks inside L0:** `log_decision_process=True` traces raw tokens; DEBUG logs print
   context words. Keep both off; `presidio-analyzer` logger ≥ INFO.
2. **Hashing:** don't use the `hash` operator (random salt → no correlation; static salt →
   TCKN space brute-forceable). Use HMAC-SHA256 with a per-tenant secret; canonicalize first
   (digits only, uppercase IBAN, E.164 phone, NFKC).
3. **Turkish case folding:** `"KİMLİK".lower()` → `"ki̇mli̇k"`, `"I".lower()` → `"i"`.
   `regex` IGNORECASE handles i/İ and ı/I for patterns; **context matching breaks** → custom
   context enhancer with Turkish folding.
4. **Context limits:** multi-word context entries never match text; only 5 preceding
   tokens count; Turkish suffixes (`kimliğim`, `numaram`) defeat substring matching
   without lemmas; `no_op` has no text context.
5. **Plates:** shipped regex gives score-1.0 false positives → own case-sensitive regex.
6. **Boundaries:** use `(?<![0-9])…(?![0-9])`, not `\b`.
7. **Cards:** default regex misses TROY (`9792`) and Mastercard 2-series → own patterns.
8. **Language gating:** mixed TR/EN text needs recognizers registered for both languages
   (or a single pattern language); Presidio does no language detection.
9. **Overlaps:** `remove_duplicates` only removes same-type containment → our own conflict
   resolution, preferring checksum-validated hits.
10. **Runtime downloads:** spaCy and HF models download on first load → vendor models into
    the image, `HF_HUB_OFFLINE=1`, block egress. Essential for on-prem.
11. **ReDoS:** `REGEX_TIMEOUT_SECONDS=1`; never accept tenant regex without validation.
12. **Unicode and offsets:** offsets are Python code points (browser = UTF-16); `\d`
    matches Arabic-Indic and full-width digits → `[0-9]` after NFKC; NFD input breaks
    Turkish letters → NFC with offset map.
13. **Default threshold 0** returns low-confidence hits → per-entity thresholds.
14. **Licenses:** avoid CC BY-SA / NC models and LGPL `python-stdnum` in the product.
