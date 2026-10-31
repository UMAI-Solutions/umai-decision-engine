"""The log alerts of deploy/azure/monitoring (ADR-0051) find log lines by their text. These tests
pin both sides: renaming a log message, a logger or the backup's output fails here instead of
silently muting an alert. The queries themselves were run against synthetic rows when written
(deploy/azure/README.md §5)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ALERTS = ROOT / "deploy" / "azure" / "monitoring" / "environment.bicep"
DCR = ROOT / "deploy" / "azure" / "monitoring.bicep"
REPORTER = ROOT / "deploy" / "azure" / "monitoring" / "health-report.sh"


def text(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


# (what the alert query looks for, the file that writes it, what that file must contain)
CONTRACT = [
    (
        "Message contains 'umai.de.pipeline decision '",
        ("src", "umai_decision_engine", "pipeline", "orchestrator.py"),
        ['getLogger("umai.de.pipeline")', '"decision request_id=', '"degraded=%s total_ms=%s"'],
    ),
    (
        "Message contains 'l1b.failed '",
        ("src", "umai_decision_engine", "l1", "stage.py"),
        ['"l1b.failed request_id=%s bundle=%s@%s kind=%s"'],
    ),
    (
        "Message contains 'l1b.shadow_failed '",
        ("src", "umai_decision_engine", "l1", "stage.py"),
        ['"l1b.shadow_failed request_id=%s event_id=%s bundle=%s@%s kind=%s"'],
    ),
    (
        "Message contains 'kind=timeout'",
        ("src", "umai_decision_engine", "l1", "decision_models", "base.py"),
        ['ErrorKind = Literal["timeout"'],
    ),
    (
        "Message contains 'admission.refused reason=quota_exceeded'",
        ("src", "umai_decision_engine", "tenancy", "admission.py"),
        ['"admission.refused reason=quota_exceeded tenant=%s environment=%s used=%d limit=%d"'],
    ),
    (
        "SyslogMessage contains 'backup: uploaded'",
        ("deploy", "vm", "umai.sh"),
        ['echo "backup: uploaded $name"'],
    ),
    (
        "SyslogMessage contains 'umai-backup.service'",
        ("deploy", "vm", "umai-backup.service"),
        ["/umai.sh backup"],  # stdout reaches syslog with ProcessName umai.sh
    ),
    (
        "parse SyslogMessage with 'container=' Container ' state=' State ' health=' Health"
        " ' restarts='",
        ("deploy", "azure", "monitoring", "health-report.sh"),
        ["container={{.Name}} state={{.State.Status}} health=", " restarts={{.RestartCount}}"],
    ),
]


@pytest.mark.parametrize(("marker", "path", "snippets"), CONTRACT, ids=lambda v: str(v)[:40])
def test_alert_marker_matches_its_writer(
    marker: str, path: tuple[str, ...], snippets: list[str]
) -> None:
    assert marker in ALERTS.read_text(encoding="utf-8"), marker
    writer = text(*path)
    for snippet in snippets:
        assert snippet in writer, f"{'/'.join(path)} no longer writes {snippet!r} ({marker})"


def test_logger_name_precedes_the_message() -> None:
    """'umai.de.pipeline decision ' relies on the logger name directly before the message."""
    assert '"%(asctime)s %(levelname)s %(name)s %(message)s"' in text(
        "src", "umai_decision_engine", "telemetry", "logging.py"
    )


def test_reporter_tag_and_facility_are_collected() -> None:
    reporter = REPORTER.read_text(encoding="utf-8")
    assert "logger -p daemon.info -t umai-health" in reporter
    dcr = DCR.read_text(encoding="utf-8")
    assert "facilityNames: ['daemon']" in dcr
    assert "ProcessName == 'umai-health'" in dcr
    assert "ProcessName == 'umai-health'" in ALERTS.read_text(encoding="utf-8")


def test_caddy_access_log_stays_on_and_loses_the_client_address() -> None:
    """The 5xx alert reads Caddy's access lines; the transform keeps status, duration, method and
    path only, so no client address reaches Log Analytics."""
    assert "\tlog {" in text("deploy", "vm", "Caddyfile")
    dcr = DCR.read_text(encoding="utf-8")
    assert 'log contains \'"logger":"http.log.access\'' in dcr
    assert "remote_ip" not in dcr and "client_ip" not in dcr
