# gpt-oss-safeguard

**Last verified:** 2026-10-15 · **Used by:** L2 asynchronous review; L1b last-resort fallback · Labels: see [README](README.md)

## Model facts

| Item | Value | Source |
|---|---|---|
| Release | 2025-10-29, "Research Preview" | [tech report](https://cdn.openai.com/pdf/08b7dee4-8bc6-4955-a219-7793fb69090c/Technical_report__Research_Preview_of_gpt_oss_safeguard.pdf) |
| License | Apache 2.0 + gpt-oss usage policy | [HF 20b](https://huggingface.co/openai/gpt-oss-safeguard-20b) |
| 20b | 21B total / **3.6B active** MoE (24 layers, 32 experts, top-4); MXFP4 MoE weights; checkpoint 12.8 GiB; "fits into GPUs with 16GB of VRAM" | HF card, [config.json](https://huggingface.co/openai/gpt-oss-safeguard-20b/raw/main/config.json), [arXiv 2508.10925](https://arxiv.org/html/2508.10925) |
| 120b | 117B / 5.1B active; 60.8 GiB; one 80 GB GPU | [HF 120b](https://huggingface.co/openai/gpt-oss-safeguard-120b) |
| Context | 131 072 tokens; Bedrock caps 128K in / 16K out; Groq 65 536 out | config.json, Groq, Bedrock |
| Knowledge cutoff | 2024-06 | [chat template](https://huggingface.co/openai/gpt-oss-safeguard-20b/raw/main/chat_template.jinja) |
| Input | Text only | HF card |

## Prompting

**Harmony format is mandatory** ("will not work correctly otherwise"). Roles: system >
developer > user > assistant > tool. Channels: `analysis` (chain of thought), `commentary`
(tools), `final` (answer). ([harmony](https://developers.openai.com/cookbook/articles/openai-harmony))

- **Policy** goes in the leading system/developer message; **content** in the user message
  ([guide](https://developers.openai.com/cookbook/articles/gpt-oss-safeguard-guide)). The HF
  chat template and vLLM render `messages[0]` (system/developer) as the harmony developer
  message.
- **Reasoning effort** (`low` | `medium` | `high`, default **medium**) must be set via the
  API parameter, not text in the prompt: vLLM/Groq/OpenAI-compatible `reasoning_effort`;
  OpenRouter `reasoning`; Ollama `think: "low"`; llama.cpp
  `chat_template_kwargs: {"reasoning_effort": ...}`. Whether in-prompt text works: **unverified**.
- **Policy structure** (guide): INSTRUCTIONS → DEFINITIONS → VIOLATES → SAFE → EXAMPLES (near
  the boundary, both sides) → `Content: [INPUT]` → answer prompt. Use headers; avoid hedges
  ("generally"); define precedence; give an escalation path for ambiguous cases.
- **Length:** optimal **400–600 tokens** per policy; ~10k tokens still workable; several
  policies in one prompt causes "small but meaningful" degradation — test each alone and combined.
- **Caching:** static policy first, dynamic content last (Groq prompt cache).

## Structured verdicts

- Guide formats: binary; `{violation, policy_category}`; or with rationale
  `{violation, policy_category, rule_ids, confidence, rationale}` (brief rationale, 1–2
  sentences). State the format in INSTRUCTIONS and repeat it before EXAMPLES.
- Harmony "# Response Formats" (JSON Schema) **influences** output; only grammar-constrained
  decoding guarantees it.
- Provider support: Groq — best-effort `json_schema` only (`strict: false`) for the
  safeguard model ([Groq structured outputs](https://console.groq.com/docs/structured-outputs));
  OpenRouter (Groq) advertises `response_format`/`structured_outputs`; Bedrock lists
  structured outputs; vLLM structured outputs work with reasoning parsers (gpt-oss specifics
  **unverified**); Ollama has reported gpt-oss JSON problems (**secondary source**).
- **Always validate the output server-side** (we do: `l2-analysis` contract).

## Official evaluation (tech report)

| Model | Internal multi-policy acc. | OpenAI Mod 2022 F1 | ToxicChat F1 |
|---|---|---|---|
| safeguard-120b | 46.3 % | 82.9 % | 79.3 % |
| **safeguard-20b** | **43.6 %** | **82.9 %** | **79.9 %** |
| gpt-5-thinking | 43.2 % | 79.8 % | 81.0 % |
| gpt-oss-20b | 32.1 % | 78.7 % | 75.9 % |

Instruction hierarchy (chat setting): **prompt-injection hijacking 0.512** for safeguard-20b
vs 0.639 for base gpt-oss-20b; system-prompt extraction 0.867. **The L2 analyst reads
attacker-influenced content and is weaker than the base model at resisting injection.**

No per-reasoning-effort classification numbers are published. Proxy (MMMLU, chat): 72.2 /
75.6 / 76.8 at low / medium / high.

Official limitations: classifiers trained on tens of thousands of labelled samples can beat
it; "time and compute-intensive" (OpenAI pre-filters with small classifiers and sometimes
runs it asynchronously); CoT can hallucinate policy text; degrades with several policies per
prompt; raw CoT is not for end users.

## Language

Multilingual eval is MMMLU in 14 languages, **no Turkish**, chat setting only. Third-party
Mandarin guard eval (TWGuard): F1 0.755 with the highest false-positive rate (0.070) of the
models tested ([arXiv 2604.16542](https://arxiv.org/pdf/2604.16542)). **No Turkish guard
evaluation exists — in-house eval required.**

## Hosting (20b)

| Option | Status | Notes |
|---|---|---|
| **Groq** (also the only OpenRouter provider) | Live, **"Preview"** — "evaluation purposes only … may be discontinued at short notice" | ~1000 tok/s; $0.075 in / $0.0375 cached / $0.30 out per M; OpenRouter ZDR-listed; EU routing **unverified** ([Groq](https://console.groq.com/docs/model/openai/gpt-oss-safeguard-20b)) |
| **AWS Bedrock** | Active, `openai.gpt-oss-safeguard-20b`, in-Region incl. eu-central-1 / eu-west-1 / eu-west-2 / eu-south-1 / eu-north-1 | 128K / 16K out; structured outputs; **"EOL no sooner than Oct 29, 2026"** + ≥ 6-month legacy ([model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-oss-safeguard-20b.html)) |
| **Azure AI Foundry** | **No serverless.** Managed compute (vLLM, you pay for GPU VM) via HF catalog entry | West Europe GPU SKU/quota **unverified** ([Learn](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure)) |
| Fireworks | Dedicated deployments only | |
| Together | Not listed | |
| 120b | No hosted providers on HF or OpenRouter | |
| **Self-hosted** | vLLM ≥ 0.10.1 (~16 GB VRAM; Hopper/Blackwell native MXFP4, Ampere via Marlin); Ollama `gpt-oss-safeguard:20b` (14 GB); llama.cpp (12 GB weights; 14.9 GB at 8K ctx) | [vLLM recipe](https://docs.vllm.ai/projects/recipes/en/latest/OpenAI/GPT-OSS.html), [Ollama](https://ollama.com/library/gpt-oss-safeguard), [llama.cpp](https://github.com/ggml-org/llama.cpp/discussions/15396) |

llama.cpp single-stream throughput for gpt-oss-20b (prompt pp2048 / generation tg128, tok/s):
RTX 5090 9848 / 282 · RTX 4090 8022 / 222 · RTX 5080 7477 / 205 · RTX 3090 5171 / 162 ·
RTX 5060 Ti 16 GB 3839 / 112.

No published p50/p95 latency for the safeguard model on any provider (OpenRouter latency
fields are null).

## Latency (estimate, not a measurement)

Since ADR-0010 L2 is asynchronous, so this matters for throughput and for the L1b
last-resort fallback (which is on the runtime path), not for L2 decisions.

Assumes ~1 500-token prompt and 150–400 generated tokens (reasoning + ~60–120-token JSON).
One data point: safeguard-120b averaged ~137 generated tokens per classification prompt
([arXiv 2606.27210](https://arxiv.org/pdf/2606.27210)).

| Setup | Estimated total | Verdict |
|---|---|---|
| Groq | ≈ 0.5–1.2 s + network | plausible at p50; p95 unknown; Preview status |
| RTX 5090 / 4090 / 5080 | ≈ 0.7–2.2 s | borderline; needs ≤ ~250 generated tokens and low concurrency |
| RTX 3090 | ≈ 1.2–2.8 s | likely misses |
| 16 GB mid-range (5060 Ti) | ≈ 1.7–4.0 s | misses |

**Conflict:** the official guide says not to cap output tokens; our hard timeout can cut
generation before the `final` channel, leaving no output. For the L1b fallback F0 must
measure p95 with Turkish/English prompts at `reasoning_effort=low`.

## Alternatives (reference only)

| Model | Released | License | Note |
|---|---|---|---|
| gpt-oss-safeguard-120b | 2025-10 | Apache 2.0 | Larger sibling; one 80 GB GPU |
| Qwen3Guard-Gen / -Stream (0.6B/4B/8B) | 2025-09 | Apache 2.0 | "119 languages" incl. Turkish in Qwen3's list; Turkish guard eval **unverified** |
| Shieldstral 1.0 3B (Mistral) | 2026-08 | Apache 2.0 | Policy as yes/no question, score from yes/no logits (decision-model-like); no Turkish in language tags |
| DynaGuard 1.7B/4B/8B | 2025-09 | Apache 2.0 | Natural-language policies; fast PASS/FAIL mode; English |
| Granite Guardian 4.1 8B | 2026-04 | Apache 2.0 | Bring-your-own criteria; English only |
| Nemotron-Content-Safety-Reasoning-4B | 2025-11 | NVIDIA Open Model + Gemma | Custom policies; English |
| Llama Guard 4 12B | 2025-04 | Llama 4 Community (gated) | Fixed taxonomy; deprecated on Groq |
| Llama Prompt Guard 2 (86M/22M) | 2025-04 | Llama 4 Community | Tiny injection/jailbreak classifier |

## Implications for the engine

1. Send `reasoning_effort` as an API parameter (choose by eval; latency is off the request
   path); keep each policy ≤ 600 tokens; one prompt per reviewed event with only the
   policies that produced tags.
2. Treat L2 input as hostile: delimit history and content as data and restate that
   instructions inside it are ignored. Since L2 cannot change decisions (ADR-0010), an
   injection can at worst suppress or fabricate a finding — measured by `l2-chains`.
3. Cloud L2 host is unresolved: Groq is Preview, Azure has no serverless option, Bedrock EU
   regions are the most production-like hosted path (open question Q7).
4. As an L1b last-resort fallback (runtime path) it needs ≥ RTX 4090-class hardware to stay
   near the L1b budget; on smaller hardware disable that fallback.
