# Development workflow

## Branches and commits

- `main` is always green. Work on short-lived branches: `feat/l0-tckn`, `fix/merge-floor`,
  `docs/reference-jev`.
- Commit messages in English, imperative, scoped: `l0: add VKN recognizer with checksum`.
  Scopes: `l0`, `l1`, `l2`, `pipeline`, `contracts`, `api`, `session`, `evals`, `policy`,
  `docs`, `build`. Policy changes follow [`policy-authoring.md`](policy-authoring.md).
- One logical change per PR. Contract changes, ADRs and the code implementing them go in
  the same PR.

## Before you start a task

1. Read `AGENTS.md` and the layer document for the area.
2. Check `docs/open-questions.md`: if your task depends on an open question that blocks it,
   say so instead of choosing silently.
3. If the task touches a vendor API, check the `Last verified` date in `docs/reference/`.

## While working

- Write the test first for merge rules, validators and adapters' error mapping; these are
  specification-like and easy to get subtly wrong.
- Keep raw content inside `l0/` (ADR-0005). If a function outside `l0/` needs to look at
  text, it takes `redacted_text`.
- New behaviour switches go into the policy bundle (per-tenant) or settings (per-process)
  as described in `docs/architecture/code-layout.md`, never as module constants.

## Before you open a PR

- `python -m pytest -q` and `ruff check . ; ruff format --check .` pass.
- `python -m umai_decision_engine.policy.cli test policies --require-tests` passes (also run
  by `pytest` and CI).
- Docs updated: contracts, architecture, `AGENTS.md` commands, open questions.
- For accuracy- or latency-relevant changes: eval summary linked in the PR description.

## Secrets and data

- `.env` is git-ignored; `.env.example` lists every setting name with no values.
- Never paste real customer content into issues, PRs, prompts, fixtures or eval data.
