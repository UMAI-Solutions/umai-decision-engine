# Turkish NLP for L0 (NER)

**Last verified:** 2026-10-15 · **Used by:** L0 (`PERSON`, `LOCATION`, `ORGANIZATION`,
address-like entities) · Labels: see [README](README.md)

NER is **off by default** in L0 (open question Q8). This page lists the options to evaluate.

## spaCy

- **No official Turkish pipeline** (spaCy lists Turkish with "none yet"; tokenizer,
  stop words, lex attributes only) ([spaCy models](https://spacy.io/usage/models)).
- Community `turkish-nlp-suite/tr_core_news_{md,lg,trf}` (NER F ≈ 88.9 / 88.9 / 91.3):
  **pinned to spaCy `>=3.4.2,<3.5.0`, which has no Python 3.12 wheels → unusable on our
  stack without retraining.** Also **CC BY-SA 4.0** (share-alike; legal review).
- `xx_ent_wiki_sm` (multilingual, MIT): Turkish coverage of its training data **unverified**.
- Blank `spacy.blank("tr")` + lookup lemmatizer (`spacy-lookups-data`, MIT) is useful for
  tokens/lemmas (context words), not NER.

## Stanza

Turkish NER (Starlang corpus; LOCATION, MONEY, ORGANIZATION, PERSON, TIME; F1 81.65)
([Stanza NER](https://stanfordnlp.github.io/stanza/ner_models.html)). Slow on CPU.

## Hugging Face models

| Model | Entities | Params | License | Reported quality |
|---|---|---|---|---|
| `akdeniz27/bert-base-turkish-cased-ner` (BERTurk) | PER, ORG, LOC | 110M | MIT | F1 0.959 |
| `akdeniz27/xlm-roberta-base-turkish-ner` | PER, ORG, LOC | 277M | MIT | F1 0.949 |
| `savasy/bert-base-turkish-ner-cased` | PER, ORG, LOC | 111M | **none declared** — avoid | F1 0.925–0.952 |
| `ytu-ce-cosmos/modernbert-tr-pii-ner` (2026-06) | **25 KVKK types** incl. TCKN, VKN, IBAN_TR, KART_NO, TELEFON, PLAKA, MERSIS_NO, ADRES, KISI_AD_SOYAD | 149M (8192 ctx) | Apache-2.0 | overlap micro-F2 80.85 % (int8: 71.90 %) |
| `newmindai/gliner2.5-kvkk-tr-v2` (2026-09) | KVKK taxonomy (118 types) | 287M | Apache-2.0 | strict F1 76.8 (author benchmark); needs the `gliner2` package, not Presidio's `GLiNERRecognizer` |
| `urchade/gliner_multi-v2.1` | zero-shot | 289M | Apache-2.0 | no Turkish evaluation (**unverified**) |
| `urchade/gliner_multi_pii-v1` (Presidio default) | PII | — | Apache-2.0 | no Turkish in card |
| `perplexity-ai/PII-Tracer` | 9 PII categories | 596M | MIT | `trust_remote_code`; Turkish **unverified** |

Avoid non-commercial licenses: `Babelscape/wikineural-multilingual-ner` (CC BY-NC-SA),
`iiiorg/piiranha-v1` (CC BY-NC-ND), GLiNER v0 `gliner_multi` (CC BY-NC).

Integration: `HuggingFaceNerRecognizer` (Presidio 2.2.362+) runs these directly, without
spaCy alignment, and is documented for Turkish:

```python
HuggingFaceNerRecognizer(
    model_name="akdeniz27/bert-base-turkish-cased-ner",
    supported_language="tr",
    label_mapping={"PER": "PERSON", "LOC": "LOCATION", "ORG": "ORGANIZATION"},
    aggregation_strategy="first",
    threshold=0.6,
    device="cpu",
)
```

## Cost

BERT-base (~110M) is ~440 MB fp32; transformer NER on CPU is roughly an order of magnitude
slower than pattern matching. Options to evaluate: ONNX/int8, GPU via `PRESIDIO_DEVICE`, and
**running NER only when the pattern stage or policy requires it**.

## Evaluation plan (feeds Q8)

Compare on the `l0-pii-tr` dataset: pattern-only vs + BERTurk NER vs + ModernBERT-TR-PII vs
+ GLiNER-KVKK; precision/recall for PERSON/ADDRESS-like entities and added p95 latency on
the reference on-prem CPU and GPU.
