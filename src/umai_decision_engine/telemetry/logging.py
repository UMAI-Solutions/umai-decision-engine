"""Structured-ish logging setup. Log ids, types, tags, latencies, decisions — never content."""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Presidio prints context words at DEBUG (docs/reference/presidio.md, pitfall 1).
    logging.getLogger("presidio-analyzer").setLevel(max(logging.WARNING, logging.getLogger().level))
