# ADR-0020 — Names and addresses start with deterministic recognizers; NER is added on evidence

- **Status:** Accepted (NER deferred)
- **Date:** 2026-10-17
- **Deciders:** product owner (UMAI)

## Context

Lakera parity (ADR-0017) needs `PERSON` and `ADDRESS`. Lakera's name detector is ML-based;
its address detector is US-only. Our candidates for an NER model are listed in
[`reference/turkish-nlp.md`](../reference/turkish-nlp.md) (BERTurk NER, ModernBERT-TR-PII,
GLiNER variants); all need `torch` + `transformers` (or `gliner`) and a 110–290M-parameter model
downloaded and shipped on-prem, and add tens of milliseconds per request on CPU. The L0 budget
is p95 < 20 ms for 4 kB. Open question Q8 is not decided, and downloading the model needs the
product owner's go-ahead.

## Decision

1. `PERSON` ships now as a **Presidio recognizer over a given-name gazetteer**
   (`l0/data/first_names.txt`, ~480 Turkish, US/UK and European given names) followed by a
   capitalised surname (particles `de` / `van` / `O'` …). Single names, honorifics
   (`Mehmet Bey`) and placeholders (`John Doe`) are not names; Turkish suffixes are excluded
   from the span (`Elif Koç'a` → `Elif Koç`).
2. `ADDRESS` ships as **street-type + house-number patterns** per region: Türkiye
   (`Mah.` / `Cad.` / `Sok.` / `No:` / `D:` / `İlçe/İl`), US, UK, DE/AT/CH, FR, ES/IT/PT, NL.
   A city or state alone is never an address.
3. An NER model is added **behind the same entity types** only when the `l0-pii-tr` eval shows
   the deterministic recall is insufficient and the added p95 latency fits the budget; the
   model choice gets its own ADR (Q8).

## Alternatives considered

| Option | Why not (now) |
|---|---|
| NER model for names and addresses from day one | Needs a model download and `torch` on-prem, adds latency to every request; not yet measured against the deterministic baseline |
| Presidio's default spaCy pipeline | English-only models; no official Turkish spaCy model; community Turkish models are pinned to an old spaCy without Python 3.12 wheels |
| No `PERSON` / `ADDRESS` until NER | Leaves two of Lakera's eight PII detectors uncovered; names are the most common PII in prompts |

## Consequences

- Names outside the gazetteer are missed. 2026-10-17: `PERSON` recall 0.80 on the dev split
  and **0.33 on the test split** — every miss an off-list name. This is below the ADR-0017
  target (0.80): NER evaluation (Q8) is the next step for `PERSON`. `ADDRESS` met its target
  on both splits (1.00). The gazetteer can grow per market; customer-specific names can be
  added with custom recognizers.
- Address recall depends on the street-type vocabulary per country; new countries need
  patterns and eval cases.
- Zero model cost: all 27 catalog types run at p95 11 ms for 4 kB of business text on the
  development machine.

## Product owner decision (2026-10-17)

Detecting names from **every culture** (Lakera's behaviour) needs a multilingual NER model; the
product owner parked name detection for now. The gazetteer recognizer stays as a free
baseline for common Turkish and European names, no NER model is downloaded or evaluated, and
`PERSON` is **excluded from the F1 exit criterion** of ADR-0017 until name detection is picked
up again (then: multilingual candidates such as `urchade/gliner_multi_pii-v1`, Apache-2.0,
measured on names from many cultures in Turkish and English sentences).

## Validation

`l0-pii-tr` per-type recall for `PERSON` and `ADDRESS` (target ≥ 0.80, ADR-0017). Revisit when
the test split or customer feedback shows names or addresses below target; then evaluate NER
candidates on recall gain and added p95.
