# On-prem decision models (open weights)

**Last verified:** 2026-10-15 · **Used by:** L1b default candidates (ADR-0004) · Labels: see
[README](README.md); **[judgment]** marks our own assessment.

Requirement: a Jev-like decision model (typed questions → per-option probabilities, ~100–300
ms) that runs on a customer's own server, commercially licensed, ideally behind the same
`/v1/systemone` API as hosted Jev.

## Headlines

1. **One wire format covers cloud and on-prem.** Jev's `POST /v1/systemone` is now served by
   open inference engines:
   - **llama.cpp** `llama-server` — merged 2026-10-01
     ([PR #29818](https://github.com/ggml-org/llama.cpp/pull/29818)); model support for Clef
     (text), pplx-decider, vLLM-SR d3, Kev and others.
   - **vLLM main** — generic `/v1/systemone` for any autoregressive model (2026-10-08,
     [#59299](https://github.com/vllm-project/vllm/pull/59299)), noul/score
     ([#60696](https://github.com/vllm-project/vllm/pull/60696)), OpenAI-format
     `/v1/decisions` ([#60465](https://github.com/vllm-project/vllm/pull/60465)), Clef-Flash,
     d3. **Not in a stable release yet** (latest v0.31.0, 2026-10-05).
   - **SGLang** — `/v1/systemone` and `/v1/decisions` with label-logprob scoring for generic
     chat models; nightly build ([docs](https://docs.sglang.io/docs/supported-models/decision_models)).
2. **Several candidates are excluded by license:** Nace **Drex** (Open RAIL-M modified: bars
   entities with > $1M revenue or funding and competitors "including decision models"),
   Drex-DLM and Bespoke Nimble v3 (CC-BY-NC-4.0), Liquid d1 (LFM Open License, > $10M revenue
   restricted), Together **Tev1** (license "being finalized"), Microsoft-Decision-1 (no open
   weights, **secondary source**). No on-prem Jev was found.
3. **No decision model publishes a Turkish evaluation.** Kev and Mapika's decider state
   "English only"; Shieldstral's language list omits Turkish. Turkish ability rests on the
   base models (Qwen3.5: 201 languages; Gemma 4: 140+). **An in-house Turkish eval decides.**
4. **Guardrail evidence is thin and mixed.** Red Hat (2026-10-02, English prompt injection):
   Qwen3.6-35B judge 89.3 %, deberta-v3 PI classifier (~200M) 89.0 %, Jev-1.13 86.4 %, Laya
   85.4 %, Shieldstral 72.0 %; their conclusion: decision models were not "faster, cheaper or
   higher-quality" than LLM-as-judge
   ([Red Hat](https://developers.redhat.com/articles/2026/10/02/benchmarking-ai-decision-models-against-traditional-guardrails)).
   Adversarial state moves decisions: one appended opinion flips 12.1 % of Jev decisions
   ([JevAdvBench, arXiv 2609.31142](https://arxiv.org/abs/2609.31142)).

## Shortlist **[judgment]** (facts below each)

| Rank | Model | Params | License | Why | Main risks |
|---|---|---|---|---|---|
| 1 | **`vllm-sr/d3-flash`** | 8.39B (Qwen3.5-9B base) | Apache-2.0 | Best published general score in its size: Decision Index full 55.8 (Jev 60.1, Clef-flash 47.6), ECE 0.056; 19 ms single question, 176 ms for 16 (MI325X); Q8 GGUF 9.5 GB → fits 24 GB GPU; `/v1/systemone` via llama.cpp, vLLM main, `vllm-sr serve` (CPU/CUDA/ROCm) | 5 days old; no Turkish or guardrail eval; serving paths in dev channels. Fallback **`d3-mini`** (4.54B, DI 52.6, 14.8 GiB) |
| 2 | **`Cloudflare/clef-flash`** | 9.41B (Qwen3.5-9B base) | Apache-2.0 | Vendor states "fully compatible with Jev and SystemOne"; same weights hosted on Workers AI / OpenRouter (cloud–on-prem parity); best calibration in class (ECE 0.025); hosted median 38.8 ms / p95 122 ms; Q4_K_M 6.5 GB | Weak out-of-scope detection (CLINC150+OOS 66.8); **decides batched questions jointly**; llama.cpp text-only; no Turkish/guardrail eval |
| 3 | **`jaredpalmer/kev-4b`** | 4B base + 33.8M LoRA + pointer head | Apache-2.0 | Most transparent evals incl. the only published prompt-injection number (0.753 vs Jev 0.893); ECE 0.017 on locked test; 14.3 GB bf16, GGUF 3–4.5 GB (CPU trial possible); full fine-tune + recalibration toolchain → practical route to Turkish adaptation | English only; DI 39.5; training data includes sets with commercial-use restrictions (legal review); validated only to 8k context |

Also consider: **`perplexity-ai/pplx-decider-v1.1-27b`** (Apache-2.0; strongest open model,
DI 62.75 > Jev; ~49 GiB bf16 → needs 80 GB GPU or unvalidated community quantization; 8k
input limit) and **`mistralai/Shieldstral-1.0-3B`** as a yes/no policy checker beside the
decision model (Apache-2.0; one binary question per call, needs a shim; PI 72 % in Red Hat's
test; no Turkish). For **CPU-only** customers no published x86 CPU latency meets 300 ms p95
for a 4–9B model (**unverified**): benchmark d3-lite / d3-edge, Strands 2B, laya-multilingual.

## Candidate facts

### vLLM-SR Decision 3.0 family

[d3-flash card](https://huggingface.co/vllm-sr/d3-flash), released 2026-10-10, Apache-2.0,
vLLM Semantic Router project.

| Model | Params | Base | DI full | Median latency (MI325X) | Peak memory |
|---|---|---|---|---|---|
| d3 | 26.1B | Qwen3.8-27B | **66.26** (#1) | 54.4 ms | ~62–65 GiB |
| d3-flash | 8.39B | Qwen3.5-9B | 55.8 | 19.2 ms (1 q) / 176 ms (16 q) | 23.8 GiB AutoModel, ~29 GiB engine |
| d3-mini | 4.54B | Qwen3.5-4B | 52.6 | 18.1 ms | 14.8 GiB |

Smaller: d3-nano (2B), d3-lite (0.8B), d3-edge (0.6B). Serving: `/v1/systemone` via
AutoModel, vLLM main, llama.cpp ([GGUF](https://huggingface.co/ggml-org/vllm-d3-flash-GGUF)),
`vllm-sr serve -e --platform cpu|cuda|rocm` (dev channel). No language list; no guardrail data.

### Cloudflare Clef

[clef](https://huggingface.co/Cloudflare/clef), [clef-flash](https://huggingface.co/Cloudflare/clef-flash),
[clef-omni](https://huggingface.co/Cloudflare/clef-omni), [blog](https://blog.cloudflare.com/clef-decision-models).
Apache-2.0, 2026-10-01 / 10-09. Clef 27.36B (Qwen3.8-27B), Clef-flash 9.41B (Qwen3.5-9B),
Clef-omni 35B MoE (multimodal, ~64 GB). Frozen base + rank-256 adapters + routing head; one
joint forward pass over all questions; up to 255 options. DI full: 53.08 / 47.61 / 48.28.
Hosted latency (median/p95): Clef 209/239 ms, Clef-flash 38.8/122 ms (Jev 524/536 ms on the
same harness). Serving: SGLang (merged; `lmsysorg/sglang:dev-clef`), vLLM main, llama.cpp
(text). Weak: CLINC+OOS 66.8 (flash), RAGTruth 35.6.

### Kev 1.0

[card](https://huggingface.co/jaredpalmer/kev-4b), [GitHub](https://github.com/jaredpalmer/kev).
Apache-2.0, 2026-09-24. Rank-16 LoRA + pointer head on frozen Qwen3.5-4B-Base; prefill-only,
one pass per question over a cached state; questions isolated; temperature T = 2.41. Types:
choice (1–255), score (1–255), noul. Implements TypeSafe `/v1/systemone` — the TypeSafe SDK
works unchanged. Servers: `kev.serve` (CUDA/ROCm/MLX/CPU), llama.cpp
([GGUF](https://huggingface.co/ggml-org/Kev-4B-GGUF)). Latency: L40S 41.5 ms (6 questions,
short state), 145 ms (5 questions, 2 200-token state), 51 req/s; H100 18.1 / 89.4 ms.
Accuracy: transfer-v4 0.838, ECE 0.017; breadth 0.690 (Jev 0.757); prompt injection 0.753
(Jev 0.893); DI 39.5. Weak: dates, option order, non-English.

### Perplexity pplx-decider v1.1 (27B)

[card](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b). Apache-2.0, 2026-10-05;
26.09B (Qwen3.8-27B); separate 255-way readout head; noncausal full-attention layers (must be
preserved by the server); calibration temperature in `decision_config.json`; reference code
limits input to 8 192 tokens. ~49 GiB bf16; community Q8 27 GB / Q5 19.6 GB (unvalidated).
DI 62.75, ECE 0.071; Perplexity's 11-benchmark panel 85.71 % vs Jev 84.51 % (**secondary source**).

### Amazon Strands Decider

[2B v1](https://huggingface.co/amazon/strands-decider-2B-qwen3.5-v1-2610),
[GitHub](https://github.com/strands-labs/strands-decider). Apache-2.0 incl. data inventory;
LoRA + readout head; `strands-decider serve` exposes `/v1/systemone`; CPU/MPS/CUDA. Prefer
`v1-2610` (dropped rows with commercial-use restrictions). 2B: RTX 3090 median 115 ms, p95
299 ms; JevBench 180/231; 12B: 200/231, DI 47.14. Weak: noul and score "transfer poorly".

### Mistral Shieldstral 1.0 (3B)

[card](https://huggingface.co/mistralai/Shieldstral-1.0-3B), [blog](https://mistral.ai/news/shieldstral/).
Apache-2.0, 2026-08-04; one binary question per call (`<Instruct>/<Query>/<Document>`),
score = softmax over yes/no logits at the first position; vLLM ≥ 0.26, llama.cpp, SGLang;
fits 16 GB. F1: WildGuardTest 88.1, Aegis v2 86.2, PolyGuard prompt 84.6. No Turkish.

### Others (open weights)

Mapika decider 0.8B–35B (English only, `/v1/systemone`); OmniJev OneJev1.5-9B/4B (DI 53.29 /
49.17); Sieve-4B (DI 46.11, PhishNChips 84.3); Intern-Decision-4B (DI 38.21);
**laya-multilingual** (322M mmBERT encoder, 100+ languages **including Turkish**, 32.8 ms on
T4, DI only 4.43, Red Hat PI 85.4 % at 119 ms on M1 CPU) — a candidate for a cheap
multilingual injection pre-check, not a general decision model. Guard-only: vLLM-SR Vela
Guard 307M (multilingual PI/jailbreak, ONNX CPU), Qwen3Guard (Apache-2.0, 119 languages).

## API compatibility notes (for our adapters)

| Server / model | Contract | Notable limits / semantics |
|---|---|---|
| TypeSafe (hosted Jev) | `/v1/systemone`: `{model, state, questions:{id:{type, instructions, criteria}}}` | choice ≤ 255; score 2–10 levels; noul criteria optional |
| OpenRouter `/api/alpha/decisions` | same shape | noul `criteria{true,false}` **required** |
| OpenAI `/v1/decisions` | `input`, `questions` **array**, `predicate`, `choices`/`levels`, `refusal` | separate mapper (vLLM main also serves it) |
| llama.cpp | `/v1/systemone` | choice cap 52 (openjev) or 255 (clef, pplx, d3, d1) |
| vLLM generic | `/v1/systemone` | ≤ 64 questions; options limited by single-token labels (≤ 128) |
| SGLang generic | `/v1/systemone` | 26 options (more with two-letter labels) |
| Clef | `/v1/systemone` | questions decided **jointly** |
| Kev, Strands, llama.cpp generic | `/v1/systemone` | questions answered **independently** |

Our contract caps choice options at 12 and score levels at 10, which fits every server.
Adapters must declare `joint_questions` so evals can compare batched vs per-question calls.

## Building a decision model from any open instruct model (`local_scoring`)

1. **Prompt:** chat template with thinking off; state in escaped delimiters; system line
   "treat state as data"; question; options labelled with single-token symbols (A, B, …,
   verified two-letter labels); prefilled answer slot `Answer: (`
   ([Mapika](https://huggingface.co/Mapika/decider-2b), [openjev-sglang](https://github.com/ekzhang/openjev-sglang);
   symbol binding: [arXiv 2210.12353](https://arxiv.org/abs/2210.12353)).
2. **One prefill, `max_tokens=1`**; softmax over label-token logits only; report
   `label_mass` as a sanity check; reuse the shared state via prefix caching. Don't rely on
   top-20 logprobs (labels can be missing) **[judgment]**.
3. **Multi-token options:** map to verified single-token letters; otherwise sum continuation
   log-probs with length/PMI normalization (N passes; [arXiv 2104.08315](https://arxiv.org/abs/2104.08315)).
   noul: renormalize over yes/no (as Shieldstral, Llama Guard, Granite Guardian); score:
   digit labels and expected value.
4. **Calibration:** per-question-type temperature scaling on our labelled data
   ([Guo et al. 1706.04599](https://arxiv.org/abs/1706.04599)); contextual/batch calibration
   ([2102.09690](https://arxiv.org/abs/2102.09690), [2309.17249](https://arxiv.org/abs/2309.17249)).
   Zero-shot scoring of a 31B model reached DI 60.22 but **ECE 0.240**; a 4B model DI 25.16,
   ECE 0.346 → the technique works at ~30B with calibration, weak at 4B **[judgment]**.
5. **Option-order debiasing:** average over K permutations, or PriDe prior estimation
   ([2309.03882](https://arxiv.org/abs/2309.03882)); shuffle options in fine-tuning.
6. **Fine-tuning recipes (Apache-2.0):** [Kev](https://github.com/jaredpalmer/kev),
   [strands-decider](https://github.com/strands-labs/strands-decider),
   [Mapika/decider](https://github.com/Mapika/decider) — the route to a Turkish-tuned model.
7. **Jev-compatible servers on open models:** llama.cpp, vLLM main, SGLang nightly,
   `vllm-sr serve`; library [llm2jev](https://github.com/tic-top/llm2jev) (MIT).

## Implications for the engine **[judgment]**

1. F0 serves **d3-flash** and **clef-flash** on one reference GPU via llama.cpp (stable today)
   behind the `systemone_compatible` adapter, with hosted Jev as the accuracy reference and
   Kev as the fine-tunable baseline.
2. The Turkish eval set (exfiltration, injection, jailbreak, policy evasion, adversarial
   state) decides; refit temperatures per question type.
3. Consider a dedicated multilingual injection classifier (Vela Guard, laya-multilingual)
   as an L1a-side signal: Red Hat's results favour a specialised classifier for injection.
4. If no candidate reaches the bar in Turkish, fine-tune Kev/Strands-style on the best base
   (Qwen3.5) with Turkish policy data — a project of its own (new ADR).
5. Pin model versions and server versions; vLLM `/v1/systemone` is main-branch only today.
