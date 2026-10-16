# Reference

Dated summaries of external facts the engine depends on. Every document has a
**Last verified** date and cites a source for each claim. Labels:

- **unverified** — could not be confirmed from any source; treat as unknown.
- **secondary source** — only press or third-party coverage, not the vendor.
- **vendor claim** — stated by the vendor, not independently measured.

Re-verify anything older than 30 days before relying on it (skill `verify-vendor-facts`).
Measurements we make ourselves go to `evals/reports/`, not here.

| Document | Covers | Used by |
|---|---|---|
| [presidio.md](presidio.md) | Microsoft Presidio analyzer/anonymizer APIs, versions, NLP engines, performance | L0 |
| [turkish-identifiers.md](turkish-identifiers.md) | TCKN, VKN, TR IBAN, phone, plate, Luhn: algorithms and synthetic test vectors | L0 |
| [turkish-nlp.md](turkish-nlp.md) | Turkish NER options (spaCy, transformers, GLiNER) | L0 (NER) |
| [onprem-decision-models.md](onprem-decision-models.md) | Open-weight decision models and DIY option scoring for on-prem L1b | L1b (default candidates) |
| [typesafe-jev.md](typesafe-jev.md) | TypeSafe Jev decision model (hosted) | L1b cloud option |
| [openai-decisions-api.md](openai-decisions-api.md) | OpenAI Decisions API (GPT-6 Luna) | L1b |
| [gpt-oss-safeguard.md](gpt-oss-safeguard.md) | gpt-oss-safeguard model, prompting, hosting, latency; alternative safety classifiers | L1b fallback, L2 |
