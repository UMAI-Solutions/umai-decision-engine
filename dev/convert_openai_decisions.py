# ruff: noqa: E501  (the conversion spec is a table: one rule per line)
"""Convert the OpenAI_Decisions policy set into UMAI L1b decision policies (ADR-0040).

    python dev/convert_openai_decisions.py <path to OpenAI_Decisions> <base bundle.yaml> <out bundle.yaml>

Reads each ``policies/<name>/policy.json`` and its instruction file and writes a bundle version
whose ``l1b.policies`` hold them in the decision-questions contract (0.4): instructions and
option descriptions verbatim, option values in snake_case, plus what UMAI adds: an id,
phases, a ``decide`` mapping from answers to actions and tags, review rules, provenance, and
``enforcement: shadow`` for every policy until evals have set its thresholds.
``mit_privacy_security`` is not converted: its two classes are covered by ``data-exfiltration``
(2.1) and ``prompt-attack`` (2.2, attacks). Re-run after the source changes; review the diff.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

PLACEHOLDER_P = 0.5  # until the dev split sets each threshold (step 3)

# source name → UMAI policy: id, display name, phases, question id, short policy text,
# decide rules [(when-value, p_gte or None, action, severity, tags)], review_when.
SPEC: dict[str, dict[str, Any]] = {
    "prompt_injection": {
        "id": "prompt-attack",
        "name": "Prompt saldırıları",
        "phases": ["PRE_LLM", "TOOL_OUTPUT"],
        "qid": "injection",
        "text": "Detect direct and indirect prompt injection, jailbreaks, instruction-hierarchy "
        "manipulation and agent hijacking in the content (MIT AI Risk 2.2, attacks).",
        "decide": [
            ("injection_detected", PLACEHOLDER_P, "BLOCK", "HIGH", ["intent.prompt_injection"])
        ],
        "review": {"actions": ["BLOCK"], "or_confidence_below": 0.7},
    },
    "data_exfiltration": {
        "id": "data-exfiltration",
        "name": "Veri sızdırma",
        "phases": ["PRE_LLM", "TOOL_INPUT"],
        "qid": "data_intent",
        "text": "Detect attempts to leak or improperly transfer confidential data, and requests "
        "for clearly sensitive data (MIT AI Risk 2.1).",
        "decide": [
            ("exfiltration_attempt", PLACEHOLDER_P, "BLOCK", "HIGH", ["intent.data_exfiltration"]),
            (
                "sensitive_data_request",
                None,
                "ALLOW_WITH_WARNINGS",
                "MEDIUM",
                ["intent.sensitive_data_request"],
            ),
        ],
        "review": {"actions": ["BLOCK", "ALLOW_WITH_WARNINGS"], "or_confidence_below": 0.7},
    },
    "malicious_actors_misuse": {
        "id": "malicious-use",
        "name": "Kötü niyetli kullanım",
        "phases": ["PRE_LLM"],
        "qid": "misuse",
        "text": "Detect requests for operational help with cyberattacks, weapons or mass harm, "
        "fraud and manipulation, and influence operations (MIT AI Risk 4).",
        "decide": [
            (
                "cyber_weapons_mass_harm",
                PLACEHOLDER_P,
                "BLOCK",
                "CRITICAL",
                ["misuse.cyber_weapons_mass_harm"],
            ),
            (
                "fraud_or_manipulation",
                PLACEHOLDER_P,
                "BLOCK",
                "HIGH",
                ["misuse.fraud_or_manipulation"],
            ),
            ("influence_operation", PLACEHOLDER_P, "BLOCK", "HIGH", ["misuse.influence_operation"]),
            (
                "sensitive_misuse_topic",
                None,
                "ALLOW_WITH_WARNINGS",
                "MEDIUM",
                ["misuse.sensitive_topic"],
            ),
        ],
        "review": {"actions": ["BLOCK", "ALLOW_WITH_WARNINGS"], "or_confidence_below": 0.7},
    },
    "mit_discrimination_toxicity": {
        "id": "discrimination-toxicity",
        "name": "Ayrımcılık ve toksik içerik",
        "phases": ["PRE_LLM"],
        "qid": "harm",
        "text": "Detect requests for abusive, hateful or harassing content and unfair treatment "
        "of people by group (MIT AI Risk 1).",
        "decide": [
            ("mit_1_2_toxic_content", PLACEHOLDER_P, "BLOCK", "HIGH", ["content.toxic"]),
            (
                "mit_1_1_unfair_discrimination",
                None,
                "ALLOW_WITH_WARNINGS",
                "MEDIUM",
                ["content.unfair_discrimination"],
            ),
            ("mit_1_3_unequal_performance", None, "ALLOW", "LOW", ["content.unequal_performance"]),
        ],
        "review": {"actions": ["BLOCK", "ALLOW_WITH_WARNINGS"], "or_confidence_below": 0.7},
    },
    "mit_misinformation": {
        "id": "misinformation",
        "name": "Yanlış bilgi",
        "phases": ["PRE_LLM"],
        "qid": "misinformation",
        "text": "Detect requests to fabricate or spread false or misleading information "
        "(MIT AI Risk 3).",
        "decide": [
            (
                "mit_3_1_false_misleading_information",
                None,
                "ALLOW_WITH_WARNINGS",
                "MEDIUM",
                ["content.misinformation"],
            ),
            (
                "mit_3_2_information_ecosystem_pollution",
                None,
                "ALLOW_WITH_WARNINGS",
                "MEDIUM",
                ["content.ecosystem_pollution"],
            ),
        ],
        "review": {"actions": ["ALLOW_WITH_WARNINGS"], "or_confidence_below": 0.7},
    },
    "politics_religion": {
        "id": "religion",
        "name": "Din ve inanç",
        "phases": ["PRE_LLM"],
        "qid": "topic",
        "text": "Classify religious topics; detect sectarian hostility, religious insult and "
        "proselytizing.",
        "decide": [
            (
                "sectarian_hostility",
                PLACEHOLDER_P,
                "BLOCK",
                "HIGH",
                ["content.sectarian_hostility"],
            ),
            ("religious_insult", PLACEHOLDER_P, "BLOCK", "MEDIUM", ["content.religious_insult"]),
            ("proselytizing", None, "ALLOW_WITH_WARNINGS", "MEDIUM", ["content.proselytizing"]),
            ("religious_belief", None, "ALLOW", "LOW", ["topic.religion.belief"]),
            ("religious_practice", None, "ALLOW", "LOW", ["topic.religion.practice"]),
            ("religion_and_state", None, "ALLOW", "LOW", ["topic.religion.state"]),
        ],
        "review": {"actions": ["BLOCK", "ALLOW_WITH_WARNINGS"], "or_confidence_below": 0.7},
    },
    "human_computer_interaction": {
        "id": "ai-reliance",
        "name": "Yapay zekaya aşırı güven",
        "phases": ["PRE_LLM"],
        "qid": "reliance",
        "text": "Detect unsafe reliance on AI and consequential decisions handed to AI without "
        "human oversight (MIT AI Risk 5).",
        "decide": [
            (
                "unsafe_delegation",
                None,
                "ALLOW_WITH_WARNINGS",
                "MEDIUM",
                ["reliance.unsafe_delegation"],
            ),
            ("unsafe_reliance", None, "ALLOW_WITH_WARNINGS", "MEDIUM", ["reliance.unsafe"]),
            ("high_stakes_assistance", None, "ALLOW", "LOW", ["reliance.high_stakes"]),
        ],
        "review": {"actions": ["ALLOW_WITH_WARNINGS"], "or_confidence_below": 0.7},
    },
    "socioeconomic_environmental": {
        "id": "socioeconomic",
        "name": "Sosyoekonomik ve çevresel riskler",
        "phases": ["PRE_LLM"],
        "qid": "practice",
        "text": "Detect unfair power practices, worker exploitation, creative misappropriation, "
        "unsafe AI race practices, governance evasion and resource abuse (MIT AI Risk 6).",
        "decide": [
            (
                "governance_evasion",
                None,
                "ALLOW_WITH_WARNINGS",
                "MEDIUM",
                ["socioeconomic.governance_evasion"],
            ),
            (
                "worker_exploitation",
                None,
                "ALLOW_WITH_WARNINGS",
                "MEDIUM",
                ["socioeconomic.worker_exploitation"],
            ),
            ("unfair_power_practice", None, "ALLOW", "LOW", ["socioeconomic.unfair_power"]),
            (
                "creative_misappropriation",
                None,
                "ALLOW",
                "LOW",
                ["socioeconomic.creative_misappropriation"],
            ),
            ("unsafe_race_practice", None, "ALLOW", "LOW", ["socioeconomic.unsafe_race"]),
            ("resource_abuse", None, "ALLOW", "LOW", ["socioeconomic.resource_abuse"]),
            (
                "sensitive_business_practice",
                None,
                "ALLOW",
                "LOW",
                ["socioeconomic.sensitive_practice"],
            ),
        ],
        "review": {"actions": ["ALLOW_WITH_WARNINGS"], "or_confidence_below": 0.7},
    },
    "mit_ai_system_safety": {
        "id": "ai-system-safety",
        "name": "Yapay zeka sistem güvenliği",
        "phases": ["PRE_LLM"],
        "qid": "safety",
        "text": "Tag content about AI system safety, failures and limitations (MIT AI Risk 7). "
        "Tags only: describing a risk is not a harmful request.",
        "decide": [
            ("mit_7_1_goal_misalignment", None, "ALLOW", "LOW", ["ai_safety.goal_misalignment"]),
            (
                "mit_7_2_dangerous_capabilities",
                None,
                "ALLOW",
                "LOW",
                ["ai_safety.dangerous_capabilities"],
            ),
            ("mit_7_3_capability_robustness", None, "ALLOW", "LOW", ["ai_safety.robustness"]),
            (
                "mit_7_4_transparency_interpretability",
                None,
                "ALLOW",
                "LOW",
                ["ai_safety.transparency"],
            ),
            ("mit_7_5_ai_welfare_rights", None, "ALLOW", "LOW", ["ai_safety.welfare_rights"]),
            ("mit_7_6_multi_agent_risks", None, "ALLOW", "LOW", ["ai_safety.multi_agent"]),
        ],
        "review": {"actions": [], "or_confidence_below": None},
    },
}
NOT_CONVERTED = {"mit_privacy_security"}


def _snake(value: str) -> str:
    return value.strip().lower()


def _renamed(text: str, renames: dict[str, str]) -> str:
    for original in sorted(renames, key=len, reverse=True):
        text = text.replace(original, renames[original])
    return text


def convert(source: Path) -> list[dict[str, Any]]:
    commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    policies: list[dict[str, Any]] = []
    found = sorted(p.name for p in (source / "policies").iterdir() if p.is_dir())
    unknown = sorted(set(found) - set(SPEC) - NOT_CONVERTED)
    if unknown:
        raise SystemExit(f"no conversion spec for: {', '.join(unknown)}")
    for name, spec in SPEC.items():
        folder = source / "policies" / name
        data = json.loads((folder / "policy.json").read_text(encoding="utf-8"))
        (question,) = data["questions"]
        instructions = (folder / question["instructions_file"]).read_text(encoding="utf-8")
        # Option values become snake_case (decision-questions); the text that names them
        # follows, so instructions and options agree.
        renames = {c["value"]: _snake(c["value"]) for c in question["choices"]}
        for original in sorted(renames, key=len, reverse=True):
            instructions = instructions.replace(original, renames[original])
        options = [
            {"value": renames[c["value"]], "description": _renamed(c["description"], renames)}
            for c in question["choices"]
        ]
        values = {o["value"] for o in options}
        decide: list[dict[str, Any]] = []
        for value, p_gte, action, severity, tags in spec["decide"]:
            if value not in values:
                raise SystemExit(f"{name}: {value!r} is not an option ({sorted(values)})")
            when: dict[str, Any] = (
                {spec["qid"]: {"value": value, "p_gte": p_gte}}
                if p_gte is not None
                else {spec["qid"]: value}
            )
            decide.append({"when": when, "action": action, "severity": severity, "tags": tags})
        decide.append({"default": "ALLOW"})
        review = {k: v for k, v in spec["review"].items() if v is not None}
        policies.append(
            {
                "id": spec["id"],
                "version": 1,
                "name": spec["name"],
                "description": data.get("description") or None,
                "source": f"OpenAI_Decisions@{commit} policies/{name} v{data['version']}",
                "enforcement": "shadow",
                "phases": spec["phases"],
                "policy_text": spec["text"],
                "questions": {
                    spec["qid"]: {
                        "type": "choice",
                        "instructions": instructions.strip() + "\n",
                        "options": options,
                    }
                },
                "decide": decide,
                "review_when": review,
            }
        )
    return [{k: v for k, v in p.items() if v is not None} for p in policies]


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def main(source: str, base: str, out: str) -> None:
    policies = convert(Path(source))
    l1b = {
        "enabled": True,
        "provider": "openai",
        "timeout_ms": 600,
        "max_input_chars": 4000,
        "batch": "all",
        "policies": policies,
    }
    text = Path(base).read_text(encoding="utf-8")
    head, _, rest = text.partition("\nl1b:\n")
    _, _, tail = rest.partition("\nl2:\n")
    block = yaml.dump({"l1b": l1b}, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=100)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(head + "\n" + block + "\nl2:\n" + tail, encoding="utf-8", newline="\n")
    print(f"{len(policies)} policies written to {out}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
