@AGENTS.md

## Claude Code specifics

- Project skills live in `.claude/skills/`. Use them for the tasks they cover:
  `add-pii-recognizer`, `add-decision-provider`, `change-contract`, `run-evals`,
  `write-adr`, `verify-vendor-facts`.
- Several products this engine depends on (TypeSafe Jev, OpenAI Decisions API) were
  released after your training cutoff. Do not answer questions about them from memory and
  do not ask the user what they are: read `docs/reference/` and verify with web search.
- For broad research (several vendors, several docs), delegate to subagents and keep the
  conclusions, not the page dumps. Write durable findings into `docs/reference/`.
- Shell is PowerShell on Windows; the Bash tool is Git Bash. Prefer commands that work in both.
