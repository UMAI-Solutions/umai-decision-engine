---
name: write-adr
description: Record an architecture decision (library, provider, schema, behaviour, threshold policy) as a numbered ADR in docs/adr. Use when choosing between alternatives or changing an earlier decision.
---

# Write an ADR

1. Next number = highest in `docs/adr/` + 1, four digits. Never renumber.
2. Copy `docs/adr/0000-template.md` to `NNNN-<kebab-title>.md`.
3. Status: `Accepted` only if the product owner decided it (in the conversation or a linked
   record); otherwise `Proposed`.
4. Context states facts and measurements with links (eval reports, reference docs). The
   alternatives table must include the option a reader would ask about first.
5. Consequences include what must now be built or measured.
6. If it supersedes an ADR, update only the old ADR's status line to `Superseded by ADR-NNNN`.
7. Add the row to `docs/adr/README.md`. Update `docs/open-questions.md` if it answers a question.
