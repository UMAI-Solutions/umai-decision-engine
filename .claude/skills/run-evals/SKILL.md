---
name: run-evals
description: Run, extend or interpret evaluation suites (L0 PII, L0 latency, L1 decisions, L1 providers/calibration, L2 chains, end-to-end, leak). Use before changing defaults, thresholds, providers or prompts, and to produce evidence for a PR or ADR.
---

# Run evals

Read first: `docs/evals/README.md`.

1. Pick the suite(s) that measure the change. Accuracy changes need the accuracy suite and
   the leak suite; latency-relevant changes need the latency suite.
2. Use the `dev` split while iterating. Run the `test` split once, at the end, and never
   tune on it.
3. Command: `python -m evals.run --suite <name> [--split dev|test] [--provider <p>] [--tag <label>]`.
   Live providers need keys in `.env`; record region and host.
4. Compare with the latest committed `evals/reports/*/summary.md` for the same suite.
5. Commit only `summary.md` (it must contain git commit, bundle versions, provider/model ids,
   region, dataset version). Raw results stay local.
6. Report in the PR: metric deltas, review rate, latency percentiles, anything that got
   worse and why that is acceptable.
7. Adding cases: synthetic only, via the generators in `evals/datasets/`; Turkish and
   English; include near-miss negatives.
