# umai-decision-engine

Layered detection and decision engine for the UMAI AI-security platform.
R&D successor of `umai-engine`.

```text
runtime:  content ──▶ L0 entity detection ──┬─ entity found ──▶ L0 gate (per-type action) ──▶ final decision
                     (Presidio, TR IDs)     │                  default BLOCK, no L1, no tokens       │
                                            └─ nothing found ─▶ L1a rules ──▶ L1b decision model ──┤
                                                                (deterministic)  (on-prem, Jev-like)  ▼ decision event
review:   selected events ──▶ L2 LLM analyst (+ session history) ──▶ analysis / finding candidate
          (asynchronous; never changes the decision)
```

- **L0** finds sensitive entities (TCKN, IBAN, phone, cards, e-mail first). If it finds any,
  the L0 gate decides right there (per-type action, default BLOCK) and L1 is skipped. Raw
  values never leave L0.
- **L1** makes the single, final runtime decision: deterministic rules first, then an
  on-prem decision model that picks from policy-defined answers with probabilities.
- **L2** reviews selected decision events afterwards, with session history, and raises
  findings (risky chains, likely false positives, policy gaps). It never changes a decision.

## Status

Standalone R&D. Not integrated with `umai-service` or any other UMAI component. Nothing
here is a product claim yet. See [`docs/roadmap.md`](docs/roadmap.md).

## Documentation

Start at [`docs/README.md`](docs/README.md). Coding agents start at [`AGENTS.md`](AGENTS.md).
