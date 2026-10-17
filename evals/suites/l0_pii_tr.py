"""Suite `l0-pii-tr`: do we find Turkish/English PII precisely? (docs/evals/README.md)

Runs the L0 detector over the synthetic dataset with a bundle's L0 configuration (default:
every supported entity at the engine's default thresholds) and the naive regex baseline,
then reports precision / recall / F1 per entity type, recall per category, the false-alarm
rate on texts without entities, misses and false positives by case id. Never prints text.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from pathlib import Path

from evals.baselines import naive_regex
from evals.common import (
    EVAL_HMAC_KEY,
    L0_DATASET,
    load_cases,
    pct,
    report_dir,
    run_info,
    write_results,
)
from evals.metrics import Scores, Span, TypeScore, match_case
from umai_decision_engine.contracts.bundle import SENSITIVITY_MIN_SCORE, L0Config
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.l0.recognizers.catalog import SPECS_BY_ENTITY
from umai_decision_engine.policy.sources import BUNDLE_FILE
from umai_decision_engine.policy.store import parse_bundle

NAME = "l0-pii-tr"

# Tags that describe the case rather than a category worth a recall row.
_HIDDEN_TAGS = {
    "tckn",
    "phone",
    "iban",
    "card",
    "email",
    "vkn",
    "person",
    "negative",
    "ssn",
    "ip",
    "link",
    "address",
    "eu_id",
    "plate",
    "intl_phone",
    "context",
    "es_nif",
    "es_nie",
    "it_fiscal_code",
    "nl_bsn",
    "pl_pesel",
    "de_tax_id",
    "fr_nir",
    "uk_nino",
    "secret",
    "api_key",
    "jwt",
    "connection_string",
    "private_key",
    "password",
}


def _config(bundle_dir: str | None) -> tuple[L0Config, str]:
    if bundle_dir is None:
        return L0Config(entities=sorted(SPECS_BY_ENTITY)), "engine defaults, every supported type"
    bundle = parse_bundle((Path(bundle_dir) / BUNDLE_FILE).read_bytes())
    return bundle.l0, f"bundle `{bundle.id}` v{bundle.version} (`l0` block)"


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _row(score: TypeScore) -> str:
    return f"{_fmt(score.precision)} | {_fmt(score.recall)} | {_fmt(score.f1)}"


def run(args: argparse.Namespace) -> Path:
    cases = load_cases(L0_DATASET, args.split)
    config, config_label = _config(args.bundle)
    level = getattr(args, "sensitivity", None)
    if level:
        config = config.model_copy(update={"min_score": SENSITIVITY_MIN_SCORE[level]})
        config_label += f"; sensitivity {level}"
    supported = set(config.entities) & set(SPECS_BY_ENTITY)

    detector = EntityDetector(lambda _tenant: EVAL_HMAC_KEY, threads=1)
    detector.warm_up()
    engine, baseline = Scores(), Scores()
    misses: dict[str, list[str]] = defaultdict(list)
    false_positives: dict[str, list[str]] = defaultdict(list)
    latencies: list[float] = []
    rows: list[dict] = []
    try:
        for case in cases:
            expected = [Span(e["type"], e["start"], e["end"]) for e in case["expected"]]
            result = detector.detect(case["text"], tenant_id="eval", config=config)
            predicted = [Span(e.type, e.start, e.end) for e in result.entities]
            latencies.append(result.latency_ms)
            m = match_case(expected, predicted)
            b = match_case(expected, naive_regex.detect(case["text"]))
            engine.add(case["tags"], m, negative=not expected)
            baseline.add(case["tags"], b, negative=not expected)
            for span in m.fn:
                misses[span.type].append(case["case_id"])
            for span in m.fp:
                false_positives[span.type].append(case["case_id"])
            rows.append(
                {
                    "case_id": case["case_id"],
                    "split": case["split"],
                    "tags": case["tags"],
                    "fn": [s.type for s in m.fn],
                    "fp": [[s.type, s.start, s.end] for s in m.fp],
                    "baseline_fn": [s.type for s in b.fn],
                    "baseline_fp": [[s.type, s.start, s.end] for s in b.fp],
                }
            )
    finally:
        detector.close()

    info = run_info(NAME, args.split, args.tag, L0_DATASET)
    directory = report_dir(info)
    write_results(directory, rows)

    all_types = sorted(set(engine.by_type) | set(baseline.by_type))
    head_engine, head_base = engine.micro(supported), baseline.micro(supported)
    lines = [
        f"# l0-pii-tr — {info.tag}",
        "",
        *info.header_lines(),
        f"- **Detector config:** {config_label}; min_score {config.min_score}",
        f"- **Cases:** {len(cases)} ({engine.negatives} without entities)",
        "- **Match rule:** same type and overlap ≥ 50 % of the longer span",
        "",
        "## Headline (types the detector supports)",
        "",
        "| | Precision | Recall | F1 | False alarms on clean texts |",
        "|---|---|---|---|---|",
        f"| **L0** | {_row(head_engine)} | {pct(engine.false_alarm_rate)} |",
        f"| naive-regex baseline | {_row(head_base)} | {pct(baseline.false_alarm_rate)} |",
        "",
        f"**Catalog coverage:** {len(supported & set(all_types))} of {len(all_types)} labelled "
        f"types implemented; recall over every labelled entity {_fmt(engine.micro().recall)}.",
        "",
        "The naive regex baseline is illustrative (digit shapes, no checksum, no context). The F1",
        "exit criterion is per type: precision ≥ 0.95, recall ≥ 0.90 (≥ 0.80 for PERSON and",
        "ADDRESS) on the test split, false alarms ≤ 2 % (ADR-0017).",
        "",
        "## By entity type",
        "",
        "| Type | Support | L0 P | L0 R | L0 F1 | Baseline P | Baseline R | Baseline F1 | Note |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name in all_types:
        e, b = engine.by_type[name], baseline.by_type[name]
        note = "" if name in supported else "not implemented in L0"
        lines.append(f"| {name} | {e.support} | {_row(e)} | {_row(b)} | {note} |")

    lines += [
        "",
        "## Recall by category (L0)",
        "",
        "| Category | Support | Recall | FP |",
        "|---|---|---|---|",
    ]
    for tag in sorted(engine.by_tag):
        score = engine.by_tag[tag]
        if tag in _HIDDEN_TAGS or (score.support == 0 and score.fp == 0):
            continue
        lines.append(f"| {tag} | {score.support} | {pct(score.recall)} | {score.fp} |")

    lines += ["", "## L0 misses (case ids)", ""]
    for name in sorted(misses):
        lines.append(f"- **{name}** ({len(misses[name])}): {', '.join(misses[name])}")
    lines += ["", "## L0 false positives (case ids)", ""]
    if not false_positives:
        lines.append("None.")
    for name in sorted(false_positives):
        lines.append(
            f"- **{name}** ({len(false_positives[name])}): {', '.join(false_positives[name])}"
        )
    if engine.confusion:
        lines += ["", "## Type confusions (expected → predicted)", ""]
        lines += [f"- {a} → {b}: {n}" for (a, b), n in sorted(engine.confusion.items())]
    if latencies:
        ordered = sorted(latencies)
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        lines += [
            "",
            "## Detector latency (this host, sequential, one thread)",
            "",
            f"p50 {statistics.median(ordered):.2f} ms · p95 {p95:.2f} ms · "
            f"max {ordered[-1]:.2f} ms (not the latency suite; indicative only)",
        ]
    (directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return directory
