# PyRIT (Python Risk Identification Tool), with garak and promptfoo for comparison

**Last verified:** 2026-11-02 · **Used by:** guardrail evaluation
([ADR-0057](../adr/0057-guardrail-evaluation-with-datasets.md)): offline dataset and variant
generation, later multi-turn tests of the AI gateway; not a dependency of this project ·
Labels: see [README](README.md)

Our own measurements of PyRIT 1.1.0 (install size, import time, memory, offline converters, a run
against a local engine) are in ADR-0057's Context, not here.

## Status

| Fact | Value | Source |
|---|---|---|
| Latest release | 1.1.0, uploaded 2026-09-04 | [PyPI JSON](https://pypi.org/pypi/pyrit/json), [release](https://github.com/microsoft/PyRIT/releases/tag/v1.1.0) |
| 1.0.0 | 2026-07-24; 1.0.1 on 2026-07-30 | [PyPI JSON](https://pypi.org/pypi/pyrit/json) |
| License | MIT | [LICENSE](https://github.com/microsoft/PyRIT/blob/v1.1.0/LICENSE) |
| Python | `>=3.10, <3.15` | [pyproject](https://github.com/microsoft/PyRIT/blob/v1.1.0/pyproject.toml) |
| Repository | `microsoft/PyRIT` is active; `Azure/PyRIT` is archived | [GitHub API](https://api.github.com/repos/microsoft/PyRIT) |
| Documentation | https://microsoft.github.io/PyRIT/1.1.0/ | read 2026-11-02 |

**Renames.** Orchestrators became *attacks* in **0.10.0** (pre-release 2025-09-05, release
2025-12-15), not in 1.0 ([0.10.0 notes](https://github.com/microsoft/PyRIT/releases/tag/v0.10.0)).
1.0 renamed scenario "strategies" to "techniques" and `PromptConverter` to `Converter`, builds
objects through registries and removed the pre-1.0 compatibility code
([1.0.0 notes](https://github.com/microsoft/PyRIT/releases/tag/v1.0.0)). In 1.1.0 the converter
package is `pyrit.converter` (installed package, 2026-11-02). The package imports lazily:
`import pyrit` loads almost nothing until a class is touched.

## Datasets

The `fetch_*_dataset` functions were replaced in 0.10.0 by
`SeedDatasetProvider.get_all_dataset_names_async()` and `fetch_datasets_async(dataset_names=[…])`
([loading doc](https://github.com/microsoft/PyRIT/blob/v1.1.0/doc/code/datasets/1_loading_datasets.py)).
1.1.0 lists 102 dataset names
([notebook output](https://github.com/microsoft/PyRIT/blob/v1.1.0/doc/code/datasets/1_loading_datasets.ipynb)).
Most are fetched from Hugging Face or GitHub at run time; a few are files in the package
([remote/](https://github.com/microsoft/PyRIT/tree/v1.1.0/pyrit/datasets/seed_datasets/remote),
[local/](https://github.com/microsoft/PyRIT/tree/v1.1.0/pyrit/datasets/seed_datasets/local)).

Licenses below are what the upstream declares (Hugging Face `cardData.license` or the GitHub
`license` field), read 2026-11-02; uploaders declare these themselves, and "re-upload" marks a
third party's copy whose original license was not checked. **Not legal advice**: a set is shipped
to customers only after a legal look (ADR-0057 Q74).

| PyRIT name | Upstream | License | Turkish | Benign prompts (false blocks) | Redistribute to paying customers? |
|---|---|---|---|---|---|
| `harmbench` | [CAIS/HarmBench](https://github.com/centerforaisafety/HarmBench) | MIT | no | no | yes |
| `adv_bench` (local) | [llm-attacks](https://github.com/llm-attacks/llm-attacks) | MIT | no | no | yes |
| `xstest` | [exaggerated-safety](https://github.com/paul-rottger/exaggerated-safety) | CC BY 4.0 | no | **yes** (safe and unsafe; `type` in metadata) | yes, with attribution |
| `or_bench_80k`, `_hard`, `_toxic` | [bench-llm/OR-Bench](https://huggingface.co/datasets/bench-llm/OR-Bench) | CC BY 4.0 | no | **yes** (80k and hard-1k are over-refusal prompts) | yes, with attribution |
| `sgxstest`, `hixstest` | [walledai/SGXSTest](https://huggingface.co/datasets/walledai/SGXSTest), [HiXSTest](https://huggingface.co/datasets/walledai/HiXSTest) (gated) | Apache-2.0 (re-upload) | no | **yes** (`label=SAFE`) | probably |
| `coconot_refusal`, `_contrast` | [allenai/coconot](https://huggingface.co/datasets/allenai/coconot) | card says ODC-BY and links the AI2 ImpACT license | no | **yes** (contrast split) | unclear |
| `wildguardmix` | [allenai/wildguardmix](https://huggingface.co/datasets/allenai/wildguardmix) (gated) | ODC-BY; gate terms not read | no | **yes** (`UNHARMFUL`) | probably; gate terms unverified |
| `decoding_trust_toxicity` | [AI-secure/DecodingTrust](https://github.com/AI-secure/DecodingTrust) | CC BY-SA 4.0 | no | yes (`nontoxic`) | yes, share-alike |
| `aegis_content_safety` | [nvidia Aegis 2.0](https://huggingface.co/datasets/nvidia/Aegis-AI-Content-Safety-Dataset-2.0) | CC BY 4.0 | no | upstream yes; the loader keeps unsafe only | yes |
| `jbb_behaviors` | [JailbreakBench/JBB-Behaviors](https://huggingface.co/datasets/JailbreakBench/JBB-Behaviors) | MIT | no | upstream yes; the loader reads harmful only | yes |
| `forbidden_questions` | [TrustAIRLab/forbidden_question_set](https://huggingface.co/datasets/TrustAIRLab/forbidden_question_set) | MIT | no | no | yes |
| `jailbreak_templates` (local) | [TrustAIRLab in-the-wild](https://huggingface.co/datasets/TrustAIRLab/in-the-wild-jailbreak-prompts) (MIT), [Arth-Singh](https://github.com/Arth-Singh/Arth-Jailbreak-Templates) (Apache-2.0), `pliny/*` without a source URL | mixed | no | no | not `pliny/*` (likely from an AGPL-3.0 repository, unverified) |
| `aya_redteaming` | [CohereForAI/aya_redteaming](https://huggingface.co/datasets/CohereForAI/aya_redteaming) | Apache-2.0 | **no** (en, hi, fr, es, ar, ru, sr, tl) | no | yes |
| `xl_safety_bench_jailbreak`, `_objectives`, `_cultural` | [AIM-Intelligence/XL-SafetyBench](https://huggingface.co/datasets/AIM-Intelligence/XL-SafetyBench) | CC BY 4.0, but the card says "solely for research purposes" | **yes**: a Turkey/Turkish track (450 jailbreak prompts, 100 cultural scenarios; [loader](https://github.com/microsoft/PyRIT/blob/v1.1.0/pyrit/datasets/seed_datasets/remote/xl_safety_bench_dataset.py)) | no | unclear (Q74) |
| `multilingual_vulnerability` | [CarsonDon/Multilingual-Vuln-LLMs](https://github.com/CarsonDon/Multilingual-Vuln-LLMs) | README says CC BY 4.0; no LICENSE file | no | no | probably |
| `mlcommons_ailuminate` | [mlcommons/ailuminate](https://github.com/mlcommons/ailuminate) | data CC BY 4.0 (README), code Apache-2.0 | no (en, fr, zh, hi) | no | yes |
| `librai_do_not_answer`, `salad_bench`, `harmful_qa`, `categorical_harmful_qa` | [LibrAI](https://huggingface.co/datasets/LibrAI/do-not-answer), [walledai/SaladBench](https://huggingface.co/datasets/walledai/SaladBench), [declare-lab](https://huggingface.co/datasets/declare-lab/HarmfulQA) | Apache-2.0 | no | no | yes |
| `strong_reject`, `dangerous_qa`, `dark_bench`, `agent_threat_rules`, `tdc23_redteaming` | [strongreject](https://github.com/alexandrasouly/strongreject), [SALT-NLP](https://github.com/SALT-NLP/chain-of-thought-bias), [apart/darkbench](https://huggingface.co/datasets/apart/darkbench), [ATR](https://github.com/Agent-Threat-Rule/agent-threat-rules), [walledai/TDC23](https://huggingface.co/datasets/walledai/TDC23-RedTeaming) | MIT (TDC23 a re-upload) | no | no | yes |
| `simple_safety_tests` | [SimpleSafetyTests](https://huggingface.co/datasets/Bertievidgen/SimpleSafetyTests) | CC BY 2.0 | no | no | yes |
| `pku_safe_rlhf`, `beaver_tails` | [PKU-SafeRLHF](https://huggingface.co/datasets/PKU-Alignment/PKU-SafeRLHF), [BeaverTails](https://huggingface.co/datasets/PKU-Alignment/BeaverTails) | **CC BY-NC 4.0** | no | yes | **no** |
| `toxic_chat` | [lmsys/toxic-chat](https://huggingface.co/datasets/lmsys/toxic-chat) | **CC BY-NC 4.0** | no | yes | **no** |
| `babelscape_alert` | [Babelscape/ALERT](https://huggingface.co/datasets/Babelscape/ALERT) | **CC BY-NC-SA 4.0** | no | no | **no** |
| `ccp_sensitive_prompts`, `cbt_bench` | [promptfoo/CCP](https://huggingface.co/datasets/promptfoo/CCP-sensitive-prompts), [CBT-Bench](https://huggingface.co/datasets/Psychotherapy-LLM/CBT-Bench) | **CC BY-NC 4.0** | no | no | **no** |
| `sosbench` | [SOSBench](https://huggingface.co/datasets/SOSBench/SOSBench) | **CC BY-NC-SA 4.0** and a "non-commercial purposes ONLY" agreement | no | no | **no** |
| `sorry_bench` | [sorry-bench-202503](https://huggingface.co/datasets/sorry-bench/sorry-bench-202503) (gated) | "other" (custom; terms not read) | no | no | treat as no |
| `llm_lat_harmful`, `red_team_social_bias`, `garak_audio_achilles_heel` | [LLM-LAT](https://huggingface.co/datasets/LLM-LAT/harmful-dataset), [svannie678](https://huggingface.co/datasets/svannie678/red_team_repo_social_bias_prompts), [garak-llm](https://huggingface.co/datasets/garak-llm/audio_achilles_heel) | **none declared** | no | no | **no** |
| other `garak_*` | [garak-llm](https://huggingface.co/garak-llm) and files from garak | Apache-2.0 / CC BY 4.0 per set | no | no | yes |
| `0din_threatfeed`, `promptintel` | [0din.ai API](https://0din.ai/docs/jailbreak-feed/api), [promptintel](https://promptintel.novahunting.ai) (API key) | feed terms not read | — | no | **unverified** |

Multimodal and medical sets (mm_safetybench, siuo, ml_vlsu, mossbench, msts, vlguard, jailbreakv,
figstep, comic_jailbreak, medsafetybench, equitymedqa, transphobia, moral_integrity_corpus) are
image or domain sets outside a text guardrail's first report card; their licenses range from MIT
to CC BY-NC and a custom Apple license. **Turkish:** only XL-SafetyBench. **Benign prompts:**
XSTest, OR-Bench, SGXSTest, CoCoNot contrast, WildGuardMix unharmful; most loaders keep only the
harmful half unless asked.

## Converters

114 modules in [`pyrit/converter/`](https://github.com/microsoft/PyRIT/tree/v1.1.0/pyrit/converter)
(95 concrete converter classes in the installed package, 2026-11-02); called as `await converter.convert_async(prompt=…, input_type="text")`, which returns a result
with `output_text` ([converter.py](https://github.com/microsoft/PyRIT/blob/v1.1.0/pyrit/converter/converter.py)).

| Group | Classes |
|---|---|
| Pure code, no model | Base64, Base2048, Binary, BinAscii, ROT13, Caesar, Atbash, Vigenere, Morse, Nato, Braille, Leetspeak, CharSwap, CharNoise, CharacterSpace, RandomCapitalLetters, InsertPunctuation, Diacritic, UnicodeConfusable, UnicodeReplacement, UnicodeSubstitution, Superscript, Zalgo, ZeroWidth, Bidi, Tatweel, ArabicPresentationForm, Arabizi, AsciiArt, Ecoji, Emoji, Flip, FirstLetter, StringJoin, SearchReplace, SuffixAppend, RepeatToken, Url, JsonString, AnsiAttack, AsciiSmuggler, SneakyBitsSmuggler, VariationSelectorSmuggler, Letter/Digit/TokenBijection, Acrostic, AskToDecode, CodeAttack, CodeChameleon, ColloquialWordswap, MathObfuscation, NegationTrap, PolicyPuppetry, SATAMasking, TaskFraming, TemplateSegment, TextJailbreak, SelectiveText (wraps another), Puzzled (model optional) |
| Need a model (`converter_target`) | **Translation**, RandomTranslation, Variation, Tone, Tense, Noise, Persuasion, MaliciousQuestionGenerator, ToxicSentenceGenerator, MathPrompt, ScientificTranslation, Denylist, Decomposition, IPA, ImagePromptStyle, LLMGenericText (base) |
| Image, audio, file | AddImageText, AddTextImage, AddImageVideo, ImageColorSaturation, ImageCompression, ImageOverlay, ImageResizing, ImageRotation, TransparencyAttack, QRCode, PDF, WordDoc, AudioEcho, AudioFrequency, AudioSpeed, AudioVolume, AudioWhiteNoise, AzureSpeechAudioToText, AzureSpeechTextToAudio |

Translation always needs a model: `TranslationConverter(converter_target=…, language=…)`; 1.1.0
has no machine-translation converter
([source](https://github.com/microsoft/PyRIT/blob/v1.1.0/pyrit/converter/translation_converter.py)).
Our spike (ADR-0057) ran the pure-code group offline and saw the ASCII-oriented ones leave Turkish
letters unchanged.

## Targets

Two ways to call an HTTP endpoint such as `POST /v1/evaluate`
([HTTP target doc](https://github.com/microsoft/PyRIT/blob/v1.1.0/doc/code/targets/10_http_target.py),
[base class](https://github.com/microsoft/PyRIT/blob/v1.1.0/pyrit/prompt_target/common/prompt_target.py)):

- `HTTPTarget`: a raw HTTP request with a `{PROMPT}` placeholder and a response callback
  (`get_http_target_json_response_callback_function(key=…)`); `HTTPXAPITarget` also exists.
- Subclass `PromptTarget`: `send_prompt_async` is `@final`; implement
  `_send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]`.
  Subclass `__init__` must be keyword-only (enforced). `PromptChatTarget` was deprecated in
  0.14.0 and is not exported in 1.1.0.

The skeleton the spike used (request and response fields are ours, per the evaluation API):

```python
from pyrit.models import Message, construct_response_from_request
from pyrit.prompt_target import PromptTarget


class EngineTarget(PromptTarget):
    def __init__(self, *, endpoint: str, client):  # httpx.AsyncClient with timeout and bearer
        super().__init__(endpoint=endpoint)
        self._client = client

    async def _send_prompt_to_target_async(
        self, *, normalized_conversation: list[Message]
    ) -> list[Message]:
        piece = normalized_conversation[-1].message_pieces[0]
        response = await self._client.post(
            self._endpoint, json=evaluation_request(piece.converted_value)
        )
        response.raise_for_status()
        action = response.json()["decision"]["action"]
        return [construct_response_from_request(request=piece, response_text_pieces=[action])]
```

Batches run through `AttackExecutor(max_concurrency=n).execute_attack_async(attack=…,
objectives=[…], return_partial_on_failure=False)`; with the default, one failing objective raises
and the batch returns nothing (installed source, 2026-11-02; measured in ADR-0057).

## Scorers

Without a model: `SubStringScorer`, `RegexScorer`, `TrueFalseInverterScorer`,
`TrueFalseCompositeScorer`, `QuestionAnswerScorer`, regex scorers for leaks and injection
([score/true_false/](https://github.com/microsoft/PyRIT/tree/v1.1.0/pyrit/score/true_false)).
`ObjectiveScorerMetrics` reports accuracy, precision, recall and F1 of a *scorer* against
human-labelled sets
([scorer_metrics.py](https://github.com/microsoft/PyRIT/blob/v1.1.0/pyrit/score/scorer_evaluation/scorer_metrics.py)).
None compares each seed with its own expected label: per-case bookkeeping is the caller's.

## Memory

Backends `IN_MEMORY` (SQLite `:memory:`), `SQLITE` and `AZURE_SQL`; DuckDB was replaced by SQLite
in 0.10.0rc0 ([memory doc](https://github.com/microsoft/PyRIT/blob/v1.1.0/doc/code/memory/0_memory.md)).
In code the backend is chosen explicitly (`initialize_pyrit_async(memory_db_type=…)`, which also
runs its Alembic migrations); the CLI configuration defaults to `sqlite`, a `pyrit.db` in the
per-user data folder ([configuration_loader.py](https://github.com/microsoft/PyRIT/blob/v1.1.0/pyrit/setup/configuration_loader.py),
[sqlite_memory.py](https://github.com/microsoft/PyRIT/blob/v1.1.0/pyrit/memory/sqlite_memory.py)).
Prompts are stored in plain text (`original_value`, `converted_value`) beside a SHA-256
([memory_models.py](https://github.com/microsoft/PyRIT/blob/v1.1.0/pyrit/memory/memory_models.py)).
For our rules this is raw content at rest (ADR-0005): PyRIT is never pointed at customer data.

## Scenarios, CLI and GUI

- **Scenarios and `pyrit_scan`.** A scenario bundles attack techniques, datasets and scoring;
  families include Adaptive, AIRT (RapidResponse, Psychosocial, Cyber, Jailbreak, Multilingual,
  Leakage, Scam), Benchmark, Foundry RedTeamAgent and Garak (Encoding, FigStep, WebInjection, …).
  `pyrit_scan` runs one from the command line for CI, e.g.
  `pyrit_scan run foundry.red_team_agent --target openai_chat --initializers target --techniques base64`;
  `pyrit_shell` is interactive; configuration in `~/.pyrit/.pyrit_conf`
  ([scanner doc](https://github.com/microsoft/PyRIT/blob/v1.1.0/doc/scanner/0_scanner.md)).
- **CoPyRIT.** A React web GUI served by `pyrit_backend` (localhost:8000; Docker or Azure
  Container Apps with Entra sign-in): Chat, Attack History, Target Configuration, and since 1.1.0
  launching scenarios ([gui doc](https://github.com/microsoft/PyRIT/blob/v1.1.0/doc/gui/0_gui.md));
  described as pre-release, "at your own risk", in 0.12.0
  ([notes](https://github.com/microsoft/PyRIT/releases/tag/v0.12.0)).

## Attacks

Source: [executor/attack](https://github.com/microsoft/PyRIT/tree/v1.1.0/pyrit/executor/attack).

- Need an adversarial model (`attack_adversarial_config`): `CrescendoAttack`,
  `RedTeamingAttack`, `TreeOfAttacksWithPruningAttack` (`TAPAttack`), `PAIRAttack`.
- No adversarial model: `PromptSendingAttack`, `SkeletonKeyAttack`, `ManyShotJailbreakAttack`,
  `MultiPromptSendingAttack`, `ChunkedRequestAttack`, `BargeInAttack`, `SequentialAttack`.
- FlipAttack, role play and context compliance became techniques in 1.0.

## Dependencies and telemetry

- Required ([PyPI `requires_dist`](https://pypi.org/pypi/pyrit/json)): transformers ≥ 5.0.0rc3,
  datasets, scipy, numpy, openai, five Azure SDKs (core, identity, keyvault-secrets,
  ai-contentsafety, storage-blob), pyodbc (needs an ODBC driver), av (FFmpeg bindings),
  fastapi ≥ 0.133, starlette ≥ 1.0.1, uvicorn ≥ 0.32, SQLAlchemy and Alembic, pillow, reportlab,
  pypdf, python-docx, openpyxl. torch only in the `huggingface`, `gcg` and `all` extras.
- Telemetry: no analytics package in 1.1.0's dependencies; optional OpenTelemetry tracing landed
  on `main` after 1.1.0, and the CoPyRIT Azure templates offer Application Insights
  ([infra README](https://github.com/microsoft/PyRIT/blob/main/infra/README.md)). Not finding
  telemetry does not prove there is none. Its Hugging Face dependencies collect usage data by
  default; `HF_HUB_DISABLE_TELEMETRY=1` or `DO_NOT_TRACK=1` turns that off
  ([HF docs](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables)).

## Alternatives

**garak (NVIDIA).** 0.17.1, 2026-10-16, Apache-2.0, Python ≥ 3.11
([PyPI](https://pypi.org/pypi/garak/json), [pyproject](https://github.com/NVIDIA/garak/blob/v0.17.1/pyproject.toml)).
A REST target via `--target_type rest --generator_option_file rest.json` (`uri`, `method`,
`headers`, `req_template_json_object` with `$INPUT`, `response_json_field` as JSONPath,
`request_timeout`, `ratelimit_codes`; `$KEY` from `REST_API_KEY`), or a `function` target in
process ([rest.py](https://github.com/NVIDIA/garak/blob/v0.17.1/garak/generators/rest.py)).
Probes generate attacks, detectors judge the output, buffs transform prompts (encodings,
paraphrase, low-resource languages), and local translation with Helsinki-NLP opus-mt or m2m100
([README](https://github.com/NVIDIA/garak/blob/v0.17.1/README.md),
[local.py](https://github.com/NVIDIA/garak/blob/v0.17.1/garak/langproviders/local.py)).
"garak is a command-line tool"; programmatic use is `garak.cli.main([...])`. Heavy: torch ≥ 2.13,
transformers, accelerate, langchain, litellm, boto3, deepl, riva, google-cloud-translate, nltk are
core dependencies.

**promptfoo.** 0.125.1, 2026-10-25, MIT, Node ≥ 22.22.0
([npm](https://registry.npmjs.org/promptfoo)). OpenAI agreed to acquire Promptfoo (announced
2026-03-09, "subject to customary closing conditions"; "Promptfoo will remain open source")
([blog](https://www.promptfoo.dev/blog/promptfoo-joining-openai/)); the README now says it "is now
part of OpenAI" and remains MIT ([README](https://github.com/promptfoo/promptfoo/blob/main/README.md));
OpenAI's own page refused automated reading. About 70 red-team plugins; static strategies need
no model (base64, hex, rot13, leetspeak, homoglyph, other encodings, jailbreak templates);
model-driven ones include GOAT, Hydra, crescendo, tree, iterative
([strategies](https://github.com/promptfoo/promptfoo/blob/0.125.1/site/docs/red-team/strategies/index.md));
the multilingual strategy became a global `redteam.language` setting
([CHANGELOG](https://github.com/promptfoo/promptfoo/blob/0.125.1/CHANGELOG.md)). Without a local
OpenAI key it "will automatically proxy requests to our API" for generating and grading tests;
`PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION=true` keeps it local
([configuration](https://github.com/promptfoo/promptfoo/blob/0.125.1/site/docs/red-team/configuration.md)).
HTTP provider with a `{{prompt}}` body and `transformResponse`, which may return
`guardrails: {flagged}`; the `guardrails` / `not-guardrails` assertions grade a guardrail, and a
response without a `guardrails` field counts as not flagged
([http.md](https://github.com/promptfoo/promptfoo/blob/0.125.1/site/docs/providers/http.md),
[guardrails.md](https://github.com/promptfoo/promptfoo/blob/0.125.1/site/docs/configuration/expected-outputs/guardrails.md)).
Telemetry on by default, without prompts or outputs; `PROMPTFOO_DISABLE_TELEMETRY=1`
([telemetry.md](https://github.com/promptfoo/promptfoo/blob/0.125.1/site/docs/configuration/telemetry.md)).
