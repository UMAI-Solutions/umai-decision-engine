# ADR-0009 — Python 3.12 / FastAPI / pip + pyproject

- **Status:** Proposed
- **Date:** 2026-10-15

## Context

`umai-engine` is FastAPI + Pydantic v2 + httpx on Python 3.12 with `requirements.txt`.
Presidio is a Python library. The team develops on Windows. `uv` is not installed on the
primary development machine.

## Decision

- Python 3.12, FastAPI, Pydantic v2 (+ pydantic-settings), httpx async, pytest
  (+ pytest-asyncio), ruff for lint and format.
- `pyproject.toml` with a `src/` layout, installed with `pip install -e ".[dev]"` in a
  `.venv`. `uv` may be used locally; nothing may require it.
- Dependencies pinned with lower and upper bounds; Presidio pinned exactly until the F1
  eval passes.

## Alternatives considered

| Option | Why not |
|---|---|
| `requirements.txt` like `umai-engine` | No package metadata, no dev extras, weaker tooling |
| Mandate `uv` | Not installed for the team today; can be adopted later without layout changes |

## Consequences

Same runtime family as `umai-engine`, which keeps the cutover simple.

## Validation

F0 scaffold builds and tests pass on Windows and Linux CI.
