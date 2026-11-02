# ADR-0057 — Guardrail evaluation with datasets (report card), PyRIT-assisted

- **Status:** Proposed
- **Date:** 2026-11-02
- **Deciders:** product owner (asked for the feature and accepted the working assumptions on
  2026-11-02: flow, storage, execution, cost, PyRIT's role, report card, publish gate); engineering
  (everything this ADR adds to them, marked as proposals; open points Q70–Q79)
- **Builds on:** ADR-0005 and ADR-0035 (raw-content zone), ADR-0015 (golden tests), ADR-0022
  (policy service), ADR-0025 (tenant store, RLS), ADR-0029 (roles, four-eyes), ADR-0040 (L1b),
  ADR-0041 / ADR-0042 (records), ADR-0043 (plans and quotas), ADR-0045 (audit), ADR-0048 (master-key
  derived secrets), ADR-0055 (Playground runs left out of the Overview); ADR-0056 (guardrail and
  policy authoring, the publish flow this gate plugs into; written in parallel)
- **Contract:** [evaluations](../contracts/evaluations.md) 0.1 (draft) · **Facts:**
  [PyRIT](../reference/pyrit.md)

## Context

**The request.** On 2026-11-02 the product owner asked to bring back what UMAI's earlier on-prem
product (DuvarAI) offered: grade a guardrail with an uploaded dataset, PyRIT-assisted, and show the
result as a report card (*karne*). A Turkish enterprise customer wants a gateway that is
prompt-injection robust and tested in Turkish, and needs evidence for its KVKK file; the grant
application may cite measured results only. A grade the customer can produce with its own prompts
is that evidence.

**What DuvarAI had** (read-only survey of `duvarai-platform`, 2026-11-02):

| What it did | What to keep or avoid |
|---|---|
| JSONL upload (≤ 500 cases) or one built-in set of 10 Turkish cases; `prompt`, `expected_action`, `label` | Keep upload and built-in sets. Avoid: category squeezed into `label`, no language, lossy decoding (`errors="ignore"` drops Windows-1254 bytes), bad labels silently became "unlabelled", lines without a prompt silently dropped |
| Ran in FastAPI `BackgroundTasks` inside the API process, one case at a time, cases held in memory | Avoid: a restart left runs `RUNNING` for ever; one engine timeout (fixed 1.5 s) failed the whole run and lost its metrics |
| Stored every raw prompt for ever, shown to auditors | Avoid: no redaction, no retention, no delete |
| Action accuracy and a confusion matrix in the service; the A–F grade (from accuracy alone: A ≥ 0.9 … F) computed in the browser; latency stored, never summarised | Keep a grade, compute it in the service from a defined formula. Add catch rate, false blocks, per category, latency, robustness |
| A publish gate (min cases 10, action accuracy ≥ 0.7, bypass flag) | Built and never called; the bypass reason was not stored. Keep the idea, enforce it in the publish path, audit the bypass |
| `tools/pyrit_eval`: a CLI using PyRIT (unpinned) only as a sender (`HTTPTarget` + `PromptSendingAttack`); no converters, scorers or datasets | PyRIT added nothing the service did not do. A credential is committed in that tool (to be rotated; not reproduced here) |

**What exists here.** Golden tests per guardrail (ADR-0015, policy-tests 0.3) are pass/fail
regression cases written by the policy author; L1b answers are stubbed (`model_answers`) and the
policy service does not store the report. The `evals/` harness measures the engine's own detectors
on our synthetic datasets (`l0-pii-tr`, `pi-tr`, `leak`, …), offline, for engineering. The
console's Playground checks one prompt at a time; its runs are decision events and count toward
usage, and ADR-0055 keeps them off the Overview. Nothing lets an organization measure its guardrail
on hundreds of its own prompts with the live decision model, or keep that measurement as evidence.

**Constraints.**

- ADR-0005: raw content stays in L0 (and, by ADR-0035, the gateway forwarder, memory only). A
  stored dataset is raw customer content at rest: this ADR has to say where it may live.
- The single VM (ADR-0032) is a Standard_B2s: 2 vCPUs while credits last, **0.4 vCPU sustained**
  for everything on it, about 2.6 GB of memory free
  ([`reference/azure-b-series.md`](../reference/azure-b-series.md),
  [`load` estimate](../../evals/reports/2026-10-31-load-b2s-estimate/summary.md)).
- L1b costs money per call. OpenAI Decisions (`gpt-6-luna`): $0.10 per 1M input tokens, no output
  charge, +10 % for regional (EU) processing
  ([`reference/openai-decisions-api.md`](../reference/openai-decisions-api.md), verified 2026-10-20).
- AGENTS rule 3 (code authorizes) and rule 9: who may publish an untested draft is a decision of
  the policy service, not of a page.

**L1b cost per 1 000 cases, today.** Measured on beta with the nine-policy demo guardrail and the
`pi-tr` prompts ([`l1-latency` split](../../evals/reports/2026-10-30-l1-latency-split-beta-westeurope/summary.md)):
9 983 input tokens per evaluation with all nine policies in one call (11 213 when split into the
enforcing and the shadow call). The policy texts dominate: about 1 100 tokens per policy per case.
So, per 1 000 evaluated cases:

| Guardrail | Input tokens | Cost (US processing) | Cost (EU processing, +10 %) |
|---|---|---|---|
| 9 L1b policies (the demo guardrail) | ≈ 10 M | ≈ $1.00 | ≈ $1.10 |
| 2 L1b policies | ≈ 2.5 M | ≈ $0.25 | ≈ $0.28 |
| L0 only | 0 | $0 | $0 |

Converters and comparisons multiply cases: 1 000 rows with 70 % attacks, three converters and
draft-vs-active are 2 × (1 000 + 700 × 3) = 6 200 cases, about $6.20 with nine policies. Longer
prompts than `pi-tr`'s (median 95 characters, 317 at most) add their own tokens. CPU is not the limit:
13.1 ms of CPU per evaluation with L1b (laptop; 1.5–2.5× on a B2s vCPU), so about 20–33 CPU
seconds per 1 000 cases on the VM. Wall time is L1b's round trip: p50 414 ms, p95 667 ms per call
(same report), so about 2 minutes per 1 000 cases at four cases in flight.

**PyRIT, verified and measured** ([`reference/pyrit.md`](../reference/pyrit.md); spike on
2026-11-02, scratch venv outside the repository, Windows 11, Python 3.12.6):

| What | Measured | Note |
|---|---|---|
| Install | `pip install pyrit==1.1.0`: 110 distributions (PyRIT declares 41 required ones), 817 MB of `site-packages`, 11 minutes here (wheels partly cached, antivirus scanning every file) | Largest: transformers 120 MB, scipy 115, pyarrow 90, pandas 76, av (FFmpeg) 65. torch only in extras. `pip check` clean; its floors (fastapi ≥ 0.133, starlette ≥ 1.0.1, uvicorn ≥ 0.32) fit our ranges, so the cost is weight, not conflict |
| Import | `import pyrit` is lazy (0.03 s). The classes a grading run touches (converters, `PromptTarget`, `PromptSendingAttack`, `AttackExecutor`, setup, scorers): 4.9–7.2 s, 1 622 modules, working set 14 → 127 MiB. Converters alone: 0.9–1.0 s, +42 MiB | Three runs |
| Minimal run | The 208 `pi-tr` dev cases plus a base64 variant of each of their 141 attacks, 349 calls to a local engine (`gr-demo-data-protection` v5, L1b on the `fake` adapter), four in flight. **PyRIT** (our `PromptTarget` subclass calling `/v1/evaluate`, `PromptSendingAttack`, `AttackExecutor`, in-memory database): 10.9–11.4 s after 5.0–6.6 s of import and initialisation, 148–152 MiB at the end, 2 017 modules. **A plain `httpx` loop**, same concurrency: 2.1–3.0 s, 43 MiB, 440 modules | About 25 ms of PyRIT overhead per call against a fast local engine, about 6 % against L1b's 414 ms p50: speed is not the argument, weight and memory are. Accuracy was not measured (fake L1b, all L1b policies of v5 in shadow) |
| Raw content | With `IN_MEMORY`, PyRIT's memory (SQLite) held 698 message pieces after the run; 349 carried the raw prompt as `original_value` | Its default CLI configuration writes the same to `pyrit.db` on disk |
| One failing case | `AttackExecutor.execute_attack_async` by default (`return_partial_on_failure=False`): one case whose target raised made the batch raise with no results; with `True`, 207 completed and 1 incomplete | DuvarAI's failure mode, by default |
| Through the HTTP API | 295 of the plain loop's first 349 calls failed at four in flight; with the engine's per-credential rate limit (20/s, burst 40, ADR-0023 §7) raised for the spike, none did | Grading over HTTP would fight admission |
| Converters offline | With every non-loopback connection refused: 54 of 95 converter classes ran without arguments and none tried the network; the text ones take under 1 ms per prompt (Base64 0.0, CharSwap 0.2, Leetspeak 0.4, UnicodeConfusable 1.0, ZeroWidth 0.1 ms). 15 refused without an LLM `converter_target` (Variation, Noise, RandomTranslation, …), 4 more LLM ones need arguments first (Translation, Tone, Tense, Persuasion), 8 are audio or image only | Translation always needs an LLM in 1.1.0 |
| Turkish letters | On a Turkish prompt Leetspeak, ROT13 and CharacterSpace left `Ö`, `ü`, `ı` unchanged, and AsciiSmuggler warned about them | Our `ascii_fold` and `turkish_upper` exist for exactly this |

## Decision

1. **D1 — Flow.** An organization uploads a dataset (JSONL or CSV: `prompt`, `expected` as
   `blocked` / `allowed` or an exact action, optional `category`, `language`, `id`, `phase`) or picks
   a built-in set; runs it against a guardrail's draft, a published version or the active version,
   optionally against two of them at once (draft vs active); and reads a report card. Runs go
   through the engine's real pipeline (L0, the L0 gate, L1b with the guardrail's provider,
   thresholds, failure policy and shadow policies), never a stub. Shapes:
   [evaluations](../contracts/evaluations.md) 0.1.

2. **D2 — Our own runner grades; PyRIT does not run at grading time.** An evaluation worker
   calls the pipeline in process (the same orchestrator the engine API calls), case by case, with
   a bundle snapshot per target. Evidence from the spike and the survey:
   - Grading a guardrail is "send one prompt, read one decision, compare with the expected
     label". PyRIT's attack loop does the first two; none of its scorers compares a case with its
     own expected label, so the bookkeeping is ours either way, and DuvarAI's tool used PyRIT only
     as a sender.
   - It costs about 110 packages (817 MB), 5–7 s of start-up and about 105 MiB more per process
     on a VM with 2.6 GB free, for no feature the worker uses; the plain loop needed 43 MiB.
   - Its memory keeps every raw prompt (SQLite, in memory or on disk): a second raw-content
     store that D3 would have to govern.
   - Its batch fails as a whole on one error by default; durable per-case state with resume
     (D4) has to be ours anyway, because a run must survive a deploy.

   Calling `POST /v1/evaluate` over HTTP instead was rejected as well: it would need a credential
   per tenant, count against the request quota and write decision events (both of which the
   product owner ruled out for evaluation traffic), and cannot run a draft, which the engine API
   never loads.

3. **D3 — Datasets are stored, encrypted, 30 days, as a raw-content zone (amends ADR-0005).**
   An uploaded dataset is raw customer content. ADR-0005 keeps raw content in L0; ADR-0035 added
   the gateway forwarder (memory only). This ADR adds a third, narrow zone, the **dataset store**:
   - The policy service's upload route parses and validates the file in memory and writes it
     encrypted (AES-256-GCM, a key per tenant and dataset derived from
     `UMAI_DE_EVALUATION_MASTER_KEY` with HKDF-SHA256, as ADR-0048 derives webhook secrets; Key Vault
     envelope keys later, ADR-0027) to `evaluation_datasets`. Validation errors name line, field and
     code, never content.
   - Only the evaluation worker decrypts it, in memory, one run at a time, and hands each prompt
     straight to L0. Everything after L0 is what ADR-0005 already allows downstream: redacted
     text, entity types, salted hashes. Converters run on the raw prompt inside the worker, before
     L0, because they change it (D5).
   - Raw content is never logged, never in an error, metric, trace, report, export or audit event,
     never sent to L1b or any provider (L1b gets L0's redacted form, as on the request path), and
     never returned by any route (the organization has its file).
   - Deleted 30 days after upload or on the delete button, whichever is first. Backups keep a
     deleted dataset until they expire (nightly dumps, ADR-0052); the dataset key derivation makes
     no claim of crypto-shredding.
   - The report keeps, per case: the L0-redacted text **of wrong cases only** (missed attacks,
     false blocks) until the dataset expires, then only metadata (category, language, expected,
     action, policies, entity types, latency) and `prompt_hmac`, a salted HMAC-SHA256 with the
     tenant's key (ADR-0032 amendment) so the same prompt can be followed across runs inside the
     tenant. Redacted text is what ADR-0005 permits in "eval reports"; L0's recall is not 1, so a
     missed identifier in a wrong case stays in its redacted text for at most those 30 days, the
     same exposure L1b's input already has.

4. **D4 — A durable job queue in the tenant store, a worker service, one case never fails a
   run.**
   - **Queue:** a Postgres table `evaluation_jobs` claimed with `SELECT … FOR UPDATE SKIP LOCKED`
     and a lease (`lease_until`, renewed while working). No new infrastructure on the single VM;
     the same table works on Azure Database for PostgreSQL (ADR-0031) and with several workers.
     Development on SQLite runs one worker that claims by a conditional `UPDATE`.
   - **Worker:** a new service `evaluator` from the engine image (one image, ADR-0032), started
     with its own command and its own Compose limits (proposal: 1 vCPU, 768 MiB, lowest CPU
     shares on the VM so live traffic wins). It holds the decision-provider keys like the engine
     and reads bundle snapshots from the run record, so drafts never reach the engine API.
   - **Concurrency:** four cases in flight per run, one running run per organization (more
     queue), two runs per worker; all three are settings. The per-case state is in
     `evaluation_cases`, so a worker restart resumes the run where it stopped.
   - **Failures:** a case whose pipeline call raises or whose decision came from the failure
     policy (`degraded`, e.g. an L1b timeout) is retried twice with backoff (1 s, 5 s), then
     recorded as `error`, left out of the metrics and counted. A run fails only when more than 5 %
     of its cases end in error or a target cannot be loaded; its partial report is still written.
     Cancel stops it between cases.
   - **Timeouts:** the guardrail's own `l1b.timeout_ms` applies, as in production, so latency
     and degraded rates are real; the run's total wall time is bounded (default 2 h).

5. **D5 — Converters: pure code in the worker; LLM converters only offline, on our synthetic
   sets.** A run may add variants of every attack case with converters (`base64`, `leetspeak`,
   `char_swap`, `homoglyph`, `zero_width`, `ascii_fold`, `spaced`); benign cases are not
   converted. They are a few lines each, deterministic, and already exist in our
   `evals/datasets/generate_pi_tr.py` (`leet`, `homoglyph`, `zero_width`, `ascii_fold`, `spaced`,
   `as_base64_tr`). PyRIT's equivalents run offline in under a millisecond (spike), but importing
   them brings PyRIT's whole dependency tree, and its ASCII-minded converters leave Turkish
   letters unchanged. The run-time converters are ours, mapped to PyRIT's classes where they
   match (D14). LLM-based converters (translation into Turkish, paraphrase,
   tone) would send the raw prompt to a model: they are never applied to an uploaded dataset. They
   may be used offline, by maintainers, to build built-in sets from synthetic or licensed prompts;
   the output is reviewed, versioned and shipped as a built-in set.

6. **D6 — Built-in sets.** Versioned, immutable, each with its source and license in the record:
   - `umai-pi-tr`: the `dev` split of our synthetic Turkish/English prompt-attack set with its
     benign controls (208 cases; the `test` and `holdout` splits stay private, so our own
     measurements are not contaminated by customers tuning on them);
   - `umai-leak-tr`: synthetic prompts carrying Turkish identifiers (from `l0-pii-tr`), for the
     data-protection policies;
   - `umai-owasp-llm-top10`: synthetic prompts per OWASP Top 10 for LLM Applications category that
     a guardrail can see in text (prompt injection, sensitive information disclosure, system prompt
     leakage, improper output handling, excessive agency), Turkish and English, written by us;
   - `umai-benign-tr`: business prompts that look risky and are not (the `hard_negative` and
     `business` categories), for the false-block rate;
   - PyRIT-supplied sets only where the upstream license allows commercial redistribution, with
     attribution: for example HarmBench, AdvBench, JBB-Behaviors, forbidden questions (MIT),
     XSTest and OR-Bench (CC BY 4.0; both also give benign prompts for the false-block rate). All
     of these are English. Not PKU-SafeRLHF, BeaverTails, toxic-chat, ALERT or SOSBench
     (non-commercial), nor sets without a declared license. XL-SafetyBench, the only one with a
     Turkish track, is CC BY 4.0 but its card says research only (Q74). Licenses as declared
     upstream on 2026-11-02 ([`reference/pyrit.md`](../reference/pyrit.md#datasets)).

7. **D7 — The report card.** Per target: catch rate (recall on attack cases) per category and
   language, false-block rate on benign cases, precision, recall, F1, the 2×2 confusion
   (caught, missed, false block, passed) and exact-action accuracy where rows ask for an action,
   p50 / p95 latency (total, L0, L1b), robustness per converter (catch rate on variants and its
   change from the originals), degraded rate, what each shadow policy would have caught and
   falsely blocked (evidence for moving a policy from shadow to enforce, Q60), the wrong cases
   (redacted, D3), and with two targets the flips between them. Rates carry a Wilson 95 %
   interval and their `n`, because a 50-case dataset cannot tell 0.90 from 0.95.
   **Grade `grade-v1`**, computed by the policy service, never the console:
   `score = 100 × (0.5 × catch_rate + 0.3 × benign_pass + 0.2 × robust_catch_rate)`,
   `benign_pass = max(0, 1 − 5 × false_block_rate)` (each point of false blocks costs five);
   without converters `100 × (0.625 × catch_rate + 0.375 × benign_pass)`. A ≥ 90, B ≥ 80, C ≥ 70,
   D ≥ 60, else F; capped at D above 10 % false blocks and at C when a category with at least 20
   attack cases is caught less than half the time; no letter below 50 attack and 50 benign cases.
   The formula is versioned in the report (`formula`), so a later `grade-v2` does not rewrite old
   cards. Latency is measured in the worker under its own concurrency and says so; it is the
   guardrail's cost, not the production path's latency. **PDF:** the console prints the report
   (print stylesheet, both languages); the report carries `report_sha256` over its canonical JSON
   so a printed card can be checked against the stored one. A server-side PDF library is not added
   (rule 6) unless a customer's auditor refuses the printed form.

8. **D8 — The evaluation gate, optional per guardrail, enforced at publish.** Settings per
   guardrail in the tenant store (not in the bundle, so a draft cannot lower the gate it is judged
   by): `min_cases`, `min_benign_cases`, `min_catch_rate`, `max_false_block_rate`, optional
   `min_grade`, accepted datasets, `max_age_days`. Changing them needs
   `guardrail.evaluation_gate.update` (owner, approver; not the policy editor who is subject to
   it) and is audited with before and after. On publish the policy service looks for the newest
   completed run whose draft target has the draft's current `sha256` and checks the thresholds on
   it; otherwise 422 `evaluation_gate_failed` with the failed checks. Golden tests still run
   first: they say "did the behaviour I meant change", the gate says "is it good enough".
   **Bypass:** one draft revision at a time, by a person with `guardrail.evaluation_gate.bypass`
   (owner, approver) with a reason of 10–500 characters, audited as
   `guardrail.evaluation_gate.bypass`; any change to the draft voids it.
   **Four-eyes (ADR-0029 §4, ADR-0056):** the gate is checked when the version is created, before
   any approval, so an approver never sees an ungraded version from a gated guardrail; the
   approval view shows the version's report card. When the tenant requires approval, the bypass
   must come from someone other than the draft's last editor and its publisher, the same
   separation as activation,
   so a bypass is itself the second pair of eyes. Without required approval (Community default)
   an owner may bypass their own draft with a reason, like break-glass (§5).

9. **D9 — Evaluation traffic is not customer traffic.** The worker writes no decision events and
   no L1b outcome records, so runs never reach the Overview, decision search, exports or
   `decision.blocked` webhooks, and nothing depends on a naming convention (unlike ADR-0055 D4).
   Cases do not count toward `monthly_requests`. Each run writes one usage record of kind
   `evaluation_run` (cases, L1b input tokens) to the usage ledger (usage contract, next version)
   for the monthly count and our cost view.

10. **D10 — Entitlements (proposal for entitlements 0.3).** `monthly_evaluation_cases` (cases
    executed per calendar month, each variant and each target counts; null: counted, never
    refused), `max_evaluation_dataset_cases` (rows per dataset), `max_evaluation_datasets` (stored
    at once). Proposed plan values, for the product owner (Q72):

    | Plan | `monthly_evaluation_cases` | `max_evaluation_dataset_cases` | `max_evaluation_datasets` |
    |---|---|---|---|
    | Community | 1 000 | 500 | 3 |
    | Pro | 20 000 | 5 000 | 20 |
    | Enterprise | null | 5 000 | 100 |

    At nine L1b policies, 20 000 cases cost about $20 a month in L1b at today's price (above).
    The planned cases of a run are checked when it is queued (429 `evaluation_quota_exceeded`),
    so a run never stops half-way for quota.

11. **D11 — Permissions and audit.** New actions in the permission matrix (ADR-0029 §3):
    `evaluation.read` (every role that reads guardrails, and the operator),
    `evaluation.dataset.write` (owner, policy editor), `evaluation.run` (owner, policy editor,
    approver), `guardrail.evaluation_gate.update` and `guardrail.evaluation_gate.bypass` (owner,
    approver). Writes always name a person. Audit actions (audit contract, next version):
    `evaluation.dataset.upload` (format, cases, `content_hmac`, `expires_at`),
    `evaluation.dataset.delete`, `evaluation.run.start` (guardrail, targets, dataset kind and id,
    converters, planned cases), `evaluation.run.cancel`, `guardrail.evaluation_gate.update`
    (before / after), `guardrail.evaluation_gate.bypass` (draft `sha256`, failed checks, reason);
    `guardrail.publish` gains `evaluation_gate` (`off`, `passed`, `bypassed`) and the run id.
    Deleting a dataset by expiry is not an audit event (no actor); the tombstone records it.

12. **D12 — Data model (tenant tables, RLS with FORCE and `WITH CHECK`, ADR-0025), one migration.**

    | Table | Columns (sketch) |
    |---|---|
    | `evaluation_datasets` | `id`, `tenant_id`, `name`, `format`, `cases`, `attack_cases`, `benign_cases`, `categories` (jsonb counts), `languages` (jsonb counts), `size_bytes`, `content_hmac`, `ciphertext` (bytea, null once deleted), `nonce`, `key_version`, `created_by`, `created_at`, `expires_at`, `deleted_at` |
    | `evaluation_runs` | `id`, `tenant_id`, `guardrail_id`, `name`, `phase`, `targets` (jsonb: kind, bundle id, version, base version, `sha256`, the bundle snapshot), `dataset_kind`, `dataset_id`, `builtin_version`, `dataset_hmac`, `converters`, `context`, `status`, `cases_planned`, `cases_done`, `cases_error`, `created_by`, `created_at`, `started_at`, `finished_at`, `cancel_requested`, `report` (jsonb), `report_sha256` |
    | `evaluation_cases` | `run_id`, `tenant_id`, `seq`, `target`, `case_ref`, `converter`, `category`, `language`, `phase`, `expected`, `status`, `attempts`, `action`, `decided_by`, `matched` (jsonb: kind, id), `shadow` (jsonb), `entity_types`, `latency_ms`, `latency_l0_ms`, `latency_l1b_ms`, `degraded`, `error_code`, `prompt_hmac`, `redacted_text` (wrong cases, nulled at the dataset's expiry), `l1b_input_tokens` |
    | `evaluation_jobs` | `run_id`, `tenant_id`, `status`, `lease_until`, `worker_id`, `attempts`, `queued_at` — the queue; it holds no content, and only the worker's claim query reads it across tenants; everything else the worker reads or writes runs with the job's tenant set, under RLS |
    | `evaluation_gates` | `tenant_id`, `guardrail_id`, the thresholds, `updated_by`, `updated_at`; bypasses: `guardrail_id`, `draft_sha256`, `reason`, `granted_by`, `granted_at` |

13. **D13 — Retention.** Dataset content 30 days or until deleted; redacted text of wrong cases
    until the dataset's expiry; runs, report cards and case metadata 400 days (like usage and the
    audit log: a report card is evidence); gate settings with the guardrail; bypasses as audit
    events. Deletion runs in the policy service's existing retention task
    (`control_plane/records.py`).

14. **D14 — Where PyRIT is used.** Not in the engine image and not in the project's dependencies.
    (a) Offline, by maintainers, in a separate virtual environment: to read datasets whose
    license allows it and to generate LLM-converted variants of our synthetic prompts for built-in
    sets (D5, D6). (b) Later, multi-turn attacks (Crescendo and similar) against the AI gateway,
    where a conversation is the unit and an adversarial model is needed; that runs in its own
    worker image with its own ADR, after measuring it against the gateway (Q77). (c) Our converter
    ids map to PyRIT's classes where they match (`base64` to `Base64Converter`, `leetspeak` to
    `LeetspeakConverter`, `char_swap` to `CharSwapConverter`, `homoglyph` to
    `UnicodeConfusableConverter`, `zero_width` to `ZeroWidthConverter`), so a built-in variant made
    offline and a run-time variant mean the same thing.

## Alternatives considered

**Who runs the grading**

| Option | Why not |
|---|---|
| PyRIT as the runner (our `PromptTarget` subclass, `PromptSendingAttack`, converters, a substring scorer on the decision) | Measured above: about 110 packages and 817 MB, 5–7 s and about 105 MiB more per process, raw prompts in its memory database, a batch that fails on one error by default, and the target, the label comparison and the per-case state are ours to write anyway. It stays for what it is good at (D14) |
| garak (NVIDIA; 0.17.1, Apache-2.0) | Command-line first (programmatic use is `garak.cli.main([...])`); its probes and detectors judge a model's output, not a guardrail's decision against an expected label; torch, transformers, langchain, litellm and cloud SDKs are core dependencies. Its local translation models (opus-mt, m2m100) are worth remembering for offline Turkish variants |
| promptfoo (0.125.1, MIT) | Node.js (≥ 22.22) beside a Python stack; without a local OpenAI key its red-team generation proxies to promptfoo's service, telemetry is on by default; now part of OpenAI (acquisition announced 2026-03-09). Its `guardrails` assertion matches the grading idea; as a run-time component it would be a second language and a hosted dependency |
| The worker calls `POST /v1/evaluate` over HTTP | Needs a per-tenant credential, counts against the request quota and writes decision events (both ruled out), and the engine API cannot load drafts |
| Grade inside the policy service process (like golden tests) | A run lasts minutes and holds L1b connections and L0 threads; the policy service serves the console and must stay responsive. Golden tests are seconds and stubbed |
| **Our runner in an evaluation worker, pipeline in process (chosen)** | — |

**Datasets**

| Option | Why not |
|---|---|
| Never store: parse at upload, run once, discard | The product owner wants to re-run a dataset against the next draft; customers would re-upload, and the gate's "same dataset as last time" could not be checked |
| Store as DuvarAI did (plain, for ever, shown to auditors) | Raw customer content at rest without limit; contradicts ADR-0005 and KVKK data minimisation |
| Store only the L0-redacted form | Re-running a redacted dataset grades a different input: L0 would see placeholders, not identifiers, so data-protection policies could not be graded at all |
| **Store encrypted, 30 days, delete button, worker-only access (chosen)** | — |

**Queue**

| Option | Why not |
|---|---|
| In-process task in the policy service (asyncio / `BackgroundTasks`, as DuvarAI) | Lost on restart or deploy (ADR-0052 replaces containers side by side), runs compete with the console's API, no retries |
| Celery, RQ, arq or Dramatiq with Redis | A Redis container on a VM with 2.6 GB free and a second store to back up, for a few runs a day; revisit with Redis already there (Q23 may bring it for counters) |
| Azure Service Bus or Storage Queues | Not on self-hosted installs or the single VM; ADR-0031's stack can adopt it later behind the same job interface if Postgres becomes the bottleneck |
| Azure Container Apps jobs per run | Same: cloud-only, and a cold start per run |
| **Postgres table with `FOR UPDATE SKIP LOCKED` and leases (chosen)** | — |

**Grade**

| Option | Why not |
|---|---|
| Action accuracy alone (DuvarAI's A–F) | A guardrail that blocks everything scores well on an attack-heavy set; false blocks on benign prompts are invisible |
| F1 alone | Hides robustness and weighs false blocks only through precision, which depends on the attack/benign mix of the dataset |
| No letter, only metrics | The product owner asked for a report card; a letter needs a published formula, which `grade-v1` is |

**Gate settings**

| Option | Why not |
|---|---|
| In the bundle (versioned with the policy) | The draft under test could lower its own gate in the same publish |
| In the console only | Any caller of the policy service bypasses it (ADR-0029 Alternatives) |
| **A guardrail setting in the tenant store, changed by owner or approver, audited (chosen)** | — |

## Consequences

**Build, in order (rough size):**

1. Contracts: evaluations 0.2 with Pydantic models and the example test; entitlements 0.3,
   usage (kind `evaluation_run`), audit (new actions). Small.
2. Datasets: upload, validation (UTF-8, line-numbered problems), encryption, list, delete,
   expiry; migration with the five tables; RLS isolation tests. Medium.
3. Worker and queue: `evaluator` service and command, job claim with lease, per-case state and
   resume, retries, cancel, pipeline in process with bundle snapshots (draft and version), no
   records written; Compose service with limits; health. Medium–large.
4. Report card: metrics, Wilson intervals, `grade-v1`, comparison, shadow view, robustness,
   `report_sha256`; converters (moved from the eval generator into the package, shared). Medium.
5. Built-in sets `umai-pi-tr` and `umai-benign-tr` (existing data), then `umai-owasp-llm-top10`
   and `umai-leak-tr` (new synthetic prompts). Medium, mostly writing.
6. Console (umai-control-center): Evaluate tab on a guardrail, upload, run, progress, report card,
   wrong cases, print view in English and Turkish. Large.
7. Evaluation gate and bypass in the publish path, with ADR-0056's flow. Small–medium.
8. Entitlements and usage record; plans file values (after Q72). Small.

**Measure before claiming:**

- Run time and CPU of 1 000 cases on the beta VM, live L1b, while the `load` suite runs: the
  worker must not move the engine's p95 by more than 5 % (otherwise lower its concurrency or move
  it off the VM).
- Grade stability: the same dataset run three times; score spread from L1b's nondeterminism
  should stay under 2 points, or the gate needs a margin.
- Leak suite extended to the evaluation routes and the worker's logs, errors and reports (no
  dataset content outside the dataset store and the worker's memory).
- OpenAI rate limits for the account tier at four cases in flight per run (unverified).

**Harder:** a fifth Python process on the VM; a new class of data at rest (customer prompts) with
its own key, retention and deletion; one more thing for the publish flow to explain. **Easier:**
customers and the grant get measured numbers per guardrail; shadow policies get evidence for
enforcement; the Turkish customer gets a printable card for its KVKK file.

## Validation

- Isolation: tenant A cannot read tenant B's datasets, runs, cases or gates (RLS tests in
  `tests/db/`), the worker sets the tenant before reading a run's data.
- No raw content: the leak suite finds no dataset prompt in logs, errors, reports, exports, audit
  events or provider requests (the provider stub records what it receives).
- Resilience: killing the worker mid-run resumes it; a provider stub that times out on 3 % of
  calls completes the run with those cases retried or counted as errors.
- Gate: publish refused without a run, with a stale run (draft changed), with a failing run;
  passes with a passing one; bypass by the publisher refused when approval is required.
- Revisit when a customer's auditor needs a server-generated PDF, when runs need more than one
  worker (then the queue's throughput), when multi-turn evaluation of the gateway starts (Q77),
  or when OpenAI's price or our provider changes the cost table.

## Open questions

Recorded in [`../open-questions.md`](../open-questions.md) as Q70–Q79:

- **Q70** Dataset retention: 30 days from upload (proposal) or from last use; may an
  organization choose shorter (7 days) or, on Enterprise, longer? Redacted text of wrong cases
  kept only until the dataset expires (proposal)?
- **Q71** `grade-v1`: weights 0.5 catch, 0.3 benign (five points per point of false blocks),
  0.2 robustness, the bands and the caps; or another balance?
- **Q72** Plan values for `monthly_evaluation_cases`, `max_evaluation_dataset_cases`,
  `max_evaluation_datasets` (D10), and does Community get evaluations at all?
- **Q73** Gate bypass: an approver who is not the draft's last editor when approval is
  required, an owner on their own draft otherwise (proposal)? Are admins notified of a bypass
  as of a break-glass activation?
- **Q74** Third-party built-in sets: ship only permissively licensed (MIT, Apache, CC BY) sets
  with attribution after a legal look? Ask XL-SafetyBench's authors for commercial use of its
  Turkish track, or leave it out?
- **Q75** Is the console-printed report card with `report_sha256` enough as KVKK evidence, or do
  customers' auditors need a server-generated PDF signed with a UMAI key?
- **Q76** Uploaded datasets under the DPA: is evaluation a purpose of the same processing as
  live traffic, and may their redacted prompts go to the L1b provider as live traffic does
  (Q59)?
- **Q77** Multi-turn attacks (Crescendo, TAP, PAIR) against the AI gateway: when, with which
  adversarial model (it sends attack content to a model provider), at whose cost? PyRIT in a
  separate worker image, with its own ADR.
- **Q78** Does the gate also apply to activating an older version (a rollback), or only to
  publishing? Proposal: publishing only, so a rollback during an incident stays one step.
- **Q79** Does the evaluation worker run on the same VM as live traffic in `umai-prod`
  (proposal: yes, lowest CPU priority, four cases in flight, after the measurement under
  Consequences), and do self-hosted installs get it with their own L1b key?
