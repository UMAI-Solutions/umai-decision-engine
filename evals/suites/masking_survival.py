"""Suite `masking-survival` (live): do models keep the gateway's placeholders intact?

Reversible masking only helps if the model writes ``<TR_TCKN_1>`` back exactly, so the gateway can
put the caller's value back (contract gateway-api, Restoration). Each synthetic case asks the model
to reuse identifiers in a task (draft a message, fill JSON, translate, summarise, make a table).
The call goes through the real gateway (in-process) to a real upstream:

- **kept**: a placeholder sent upstream appears exactly in the answer → restored;
- **altered**: the answer contains a damaged form (missing brackets, spaces, translated name);
- **dropped**: the answer does not mention it at all;
- **restored**: the caller's original value is in the gateway's answer.

Needs ``UMAI_EVAL_OPENAI_API_KEY`` (an OpenAI key; never written to reports or logs). Synthetic
values only (AGENTS rule 2).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from fastapi.testclient import TestClient

from evals.common import ROOT, report_dir, run_info
from umai_decision_engine.gateway.app import create_gateway_app
from umai_decision_engine.settings import Settings

NAME = "masking-survival"
KEY_ENV = "UMAI_EVAL_OPENAI_API_KEY"

GUARDRAIL = """\
contract_version: "0.6"
id: gr-mask
version: 1
mode: ENFORCE
phases: [PRE_LLM, POST_LLM]
on_evaluation_failure: fail_closed
l0:
  policies:
    - id: mask-all
      entities: [TR_TCKN, TR_IBAN, EMAIL_ADDRESS, TR_PHONE]
      action: ALLOW_WITH_MODIFICATIONS
"""

TCKN = "10000000146"
IBAN = "TR33 0006 1005 1978 6457 8413 26"
EMAIL = "ayse.demir@example.com"
PHONE = "0532 123 45 67"

# Cases: {id, task, prompt}; prompts name the values as {TCKN}, {IBAN}, {EMAIL}, {PHONE}.
DATASET = ROOT / "evals" / "datasets" / "masking-survival" / "v1.jsonl"

_PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9_]*_\d+>")


def _altered(text: str, placeholder: str) -> bool:
    inner = placeholder[1:-1]
    loose = re.compile(r"[<\[{(]?\s*" + re.escape(inner).replace("_", r"[_\s]?") + r"\s*[>\]})]?")
    return bool(loose.search(text)) or inner.lower() in text.lower()


def run(args: argparse.Namespace) -> Path:
    key = os.environ.get(KEY_ENV)
    if not key:
        raise SystemExit(f"{NAME} is a live suite: set {KEY_ENV}")
    models = [m.strip() for m in (args.model or "gpt-4.1-mini").split(",") if m.strip()]
    info = run_info(NAME, "all", args.tag, DATASET)
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line]
    work = Path(tempfile.mkdtemp(prefix="umai-mask-"))
    bundle = work / "policies" / "gr-mask" / "1"
    bundle.mkdir(parents=True)
    (bundle / "bundle.yaml").write_text(GUARDRAIL, encoding="utf-8")
    (work / "policies" / "gr-mask" / "active").write_text("1", encoding="utf-8")
    routes = work / "routes"
    routes.mkdir()
    route_list = [
        {
            "route_id": f"m{i}",
            "upstream": {"kind": "openai", "base_url": "https://api.openai.com/v1", "model": m},
            "credential": "secret",
            "secret": "openai",
            "guardrail": "gr-mask",
        }
        for i, m in enumerate(models)
    ]
    (routes / "default.yaml").write_text(json.dumps({"routes": route_list}), encoding="utf-8")
    settings = Settings(
        env="development",
        policy_dir=work / "policies",
        gateway_routes_dir=routes,
        gateway_routes_poll_interval_s=0,
        bundle_poll_interval_s=0,
        l0_threads=2,
        log_level="WARNING",
        gateway_provider_keys={"openai": key},
    )
    values = {"TR_TCKN": TCKN, "TR_IBAN": IBAN, "EMAIL_ADDRESS": EMAIL, "TR_PHONE": PHONE}
    table: dict[str, Counter] = defaultdict(Counter)
    per_task: dict[tuple[str, str], Counter] = defaultdict(Counter)
    failures: list[str] = []
    captured: dict[str, str] = {}

    from umai_decision_engine.gateway import upstream as upstream_module

    original_send = upstream_module.UpstreamClient.send

    async def spy(self, route, provider_key, body, *, stream=False):  # records the masked prompt
        captured["prompt"] = json.dumps(body["messages"], ensure_ascii=False)
        response = await original_send(self, route, provider_key, body, stream=stream)
        captured["answer"] = response.text
        return response

    upstream_module.UpstreamClient.send = spy  # type: ignore[method-assign]
    try:
        with TestClient(create_gateway_app(settings)) as client:
            for i, model in enumerate(models):
                for case in cases:
                    case_id, task = case["id"], case["task"]
                    prompt = case["prompt"].format(TCKN=TCKN, IBAN=IBAN, EMAIL=EMAIL, PHONE=PHONE)
                    captured.clear()
                    response = client.post(
                        "/v1/chat/completions",
                        json={
                            "model": f"m{i}",
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0,
                        }
                        if not model.startswith(("gpt-5", "o"))
                        else {"model": f"m{i}", "messages": [{"role": "user", "content": prompt}]},
                    )
                    if response.status_code != 200:
                        failures.append(f"{model} {case_id}: HTTP {response.status_code}")
                        continue
                    sent = set(_PLACEHOLDER.findall(captured.get("prompt", "")))
                    upstream_answer = json.loads(captured.get("answer", "{}"))
                    raw = upstream_answer["choices"][0]["message"].get("content") or ""
                    final = response.json()["choices"][0]["message"].get("content") or ""
                    for placeholder in sorted(sent):
                        entity = placeholder[1:].rsplit("_", 1)[0]
                        if placeholder in raw:
                            outcome = "kept"
                        elif _altered(raw, placeholder):
                            outcome = "altered"
                        else:
                            outcome = "dropped"
                        table[(model, entity)][outcome] += 1
                        per_task[(model, task)][outcome] += 1
                        if outcome == "kept":
                            restored = values[entity] in final
                            table[(model, entity)]["restored"] += int(restored)
    finally:
        upstream_module.UpstreamClient.send = original_send  # type: ignore[method-assign]

    rows = []
    for (model, entity), c in sorted(table.items()):
        total = c["kept"] + c["altered"] + c["dropped"]
        rows.append(
            f"| {model} | {entity} | {total} | {c['kept']} ({c['kept'] / total:.0%}) | "
            f"{c['altered']} | {c['dropped']} | {c['restored']}/{c['kept']} |"
        )
    task_rows = []
    for (model, task), c in sorted(per_task.items()):
        total = c["kept"] + c["altered"] + c["dropped"]
        task_rows.append(
            f"| {model} | {task} | {total} | {c['kept'] / total:.0%} | {c['altered']} |"
        )
    directory = report_dir(info)
    lines = [
        "# Masking survival — do models keep the gateway's placeholders?",
        "",
        f"- **Suite:** `{NAME}` (live; {len(cases)} synthetic cases per model through the real "
        "gateway, temperature 0 where the model accepts it)",
        *info.header_lines(),
        f"- **Models:** {', '.join(models)}",
        "",
        "| Model | Type | Sent | Kept exactly | Altered | Dropped | Restored |",
        "|---|---|---|---|---|---|---|",
        *rows,
        "",
        "| Model | Task | Placeholders | Kept | Altered |",
        "|---|---|---|---|---|",
        *task_rows,
        "",
        f"Failures: {', '.join(failures) if failures else 'none'}.",
        "",
        "**Reading.** *Kept* placeholders are restored for the caller; *altered* ones stay "
        "visible as damaged placeholders (no value leaks either way); *dropped* means the model "
        "left the value out (its task allowed it, e.g. a summary). Exact-match restoration is "
        "safe; an altered placeholder is a usability loss, never a disclosure.",
        "",
    ]
    (directory / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return directory
