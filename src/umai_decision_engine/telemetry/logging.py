"""Structured-ish logging setup. Log ids, types, tags, latencies, decisions — never content."""

from __future__ import annotations

import logging

ACCESS_LOGGER = "uvicorn.access"


class AccessPathOnly(logging.Filter):
    """uvicorn's access line names the path with its query string; a query can carry what a
    person typed (the policy service's decision search ``q``). Keep the path, drop the query
    (launch plan S0-4). uvicorn logs ``'%s - "%s %s HTTP/%s" %d'`` with the path third."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            path, _, query = args[2].partition("?")
            if query:
                record.args = (args[0], args[1], path + "?[query]", args[3], args[4])
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Presidio prints context words at DEBUG (docs/reference/presidio.md, pitfall 1).
    logging.getLogger("presidio-analyzer").setLevel(max(logging.WARNING, logging.getLogger().level))
    access = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, AccessPathOnly) for f in access.filters):
        access.addFilter(AccessPathOnly())
