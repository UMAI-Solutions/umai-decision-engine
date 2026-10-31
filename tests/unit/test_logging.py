"""Logging setup: access lines without query strings (launch plan S0-4). Synthetic data only."""

from __future__ import annotations

import logging

from umai_decision_engine.telemetry.logging import ACCESS_LOGGER, configure_logging

TCKN = "10000000146"  # synthetic test vector


def _access_line(target: str) -> None:
    # uvicorn's own call (protocols/http/h11_impl.py and httptools_impl.py)
    logging.getLogger(ACCESS_LOGGER).info(
        '%s - "%s %s HTTP/%s" %d', "203.0.113.10:50000", "GET", target, "1.1", 422
    )


def test_access_lines_keep_the_path_and_drop_the_query(caplog) -> None:
    configure_logging("INFO")
    configure_logging("INFO")  # idempotent: one filter, not two
    assert len(logging.getLogger(ACCESS_LOGGER).filters) == 1
    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
        _access_line(f"/v1/decision-events?q=TC%20{TCKN}&limit=5")
        _access_line("/v1/tenants/tenant-a/keys")
    assert TCKN not in caplog.text
    assert '"GET /v1/decision-events?[query] HTTP/1.1" 422' in caplog.text
    assert '"GET /v1/tenants/tenant-a/keys HTTP/1.1" 422' in caplog.text
