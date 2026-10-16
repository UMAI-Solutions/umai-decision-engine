# Roadmap (R&D)

Phases are sequential in their exit criteria, but work inside a phase can run in parallel.
Each phase ends with a short report in `evals/reports/` or `docs/` and an update to
[`open-questions.md`](open-questions.md).

| Phase | Scope | Exit criteria |
|---|---|---|
| **F0 — Foundations** | Scaffold (`pyproject.toml`, `src/` layout, CI, ruff, pytest); Pydantic contracts from `docs/contracts/`; fake providers; eval harness and synthetic Turkish/English datasets; reference on-prem hardware; serve 2–3 open-weight decision models on `/v1/systemone` (llama.cpp / vLLM / SGLang) next to hosted Jev as reference | `pytest` green on Windows + Linux; contracts as code; `l1-providers` report; default on-prem L1b model recorded in ADR-0004 |
| **F1 — L0** | Presidio analyzer factory (`no_op` engine); normalization; core recognizers (TCKN, TR IBAN, phone, card incl. TROY, e-mail) then extended (VKN, secrets); validators; Turkish context scoring; HMAC hashing; redaction | `l0-pii-tr` beats the regex baseline on precision at equal or better recall; `l0-latency` p95 within budget; leak suite 0 |
| **F2 — L1** | Facts builder; L1a rule engine (AGT-compatible operators, legacy preflight, redaction rules); question compiler; `systemone_compatible` adapter (on-prem server + hosted Jev), `openai_decisions`, `gpt_oss_safeguard`, `fake`; mapping, review selection, shadow providers; decision event emission | `l1-decisions` ≥ baseline; runtime p95 measured on reference hardware; calibration report |
| **F3 — L2 review** | Event sink + review queue (SQLite, lease/ack, idempotent); session history store; L2 worker; prompt v1; analysis validation and leak check; finding candidates | `l2-chains` shows history helps with an acceptable false-finding rate; worker metrics under load; redelivery creates no duplicates |
| **F4 — Replacement readiness** | Compat endpoint and adapters for `umai-engine` callers; `GuardrailSnapshot` → bundle converter; durable event delivery beyond SQLite; load test; replay/shadow against `umai-engine` decisions; SLO proposal for the runtime path | An `umai-engine` caller can switch engines by URL with no model changes; decision diff vs `umai-engine` reviewed |
| **F5 — Policy control plane** | Dashboard + API for drafts, validation, golden tests, playground, impact preview, approval, publish (sign), activate, rollback; `http_pull` bundle source + push signal (ADR-0013) | A policy edited in the dashboard is live on all engines within the target time; rollback by pointer |

## Progress

| Date | Done | Notes |
|---|---|---|
| 2026-10-16 | L0 recall fixes from manual testing: unvalidated TCKN with ID context; bare mobile numbers without context; `90` prefix; content-hash bundle fingerprint (same-size pointer flips were missed) | 157 tests. Systematic recall/precision needs the `l0-pii-tr` eval set (F0) |
| 2026-10-16 | L0 gate (ADR-0014): per-type `on_detect`, deterministic exceptions, masking via `output_modifications`; shared `rules/` condition evaluator; bundle 0.4, API 0.4; demo bundle v2 | 148 tests |
| 2026-10-16 | Scaffold (pyproject, src layout, ruff, pytest — 113 tests); contracts (evaluation API 0.3, bundle 0.3, entity detection 0.2); bundle store (directory source, SHA-256 + Ed25519, hot reload, active pointer, last-known-good); L0 core entities with Turkish context scoring; API endpoints | F0/F1 partially; no eval harness yet; L1 returns `L1_NOT_IMPLEMENTED` for bundles with L1 logic. L0 latency: [`evals/reports/2026-10-16-l0-latency-dev/summary.md`](../evals/reports/2026-10-16-l0-latency-dev/summary.md) |

Nothing in this repository is a customer-facing claim until F4 measurements exist (no SLO
goes into a customer contract before it is measured).
