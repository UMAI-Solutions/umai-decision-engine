---
name: verify-vendor-facts
description: Re-verify and update external facts in docs/reference (TypeSafe Jev, OpenAI Decisions API, gpt-oss-safeguard, Presidio, Turkish NLP models) — API shapes, prices, limits, availability, data handling. Use before relying on a reference doc older than 30 days or when a provider call fails unexpectedly.
---

# Verify vendor facts

These products change fast and several post-date model training. Never fill gaps from memory.

1. Open the reference doc; note `Last verified` and every claim marked `unverified` or
   `secondary source`.
2. Check primary sources first (vendor docs, API reference, changelog, model card, PyPI,
   GitHub releases), then secondary sources. Use web search and fetch; for OpenRouter
   metadata, `curl https://openrouter.ai/api/v1/models` and
   `https://openrouter.ai/api/v1/models/<id>/endpoints`.
3. Update the doc: changed facts, new `Last verified` date, source URL next to each claim.
   Keep `unverified` labels where you could not confirm.
4. If an API shape changed, open or update the item in `docs/open-questions.md` and check
   the adapter in `src/umai_decision_engine/l1/decision_models/` against it.
5. If a change affects a decision (price, availability, on-prem, data retention), say so in
   the summary and suggest an ADR.
