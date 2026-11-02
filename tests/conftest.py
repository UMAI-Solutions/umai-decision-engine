"""Shared fixtures. All content is synthetic (AGENTS.md rule 2)."""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

from umai_decision_engine.contracts.bundle import L0Config
from umai_decision_engine.l0.detector import EntityDetector

CORE = ["TR_TCKN", "TR_IBAN", "IBAN_CODE", "TR_PHONE", "CREDIT_CARD", "EMAIL_ADDRESS"]

# Exponential on "1…1!" yet accepted by the bundle-load check (ADR-0056's ReDoS measurements).
CATASTROPHIC_PATTERNS = (r"(\w|\d)+$", r"([a-z0-9]{1,20})+$")


def bundle_yaml(
    bundle_id: str = "gr-test",
    version: int = 1,
    *,
    mode: str = "ENFORCE",
    failure: str = "fail_closed",
    l1a_rules: str = "[]",
    entities: list[str] | None = None,
    l0_extra: str = "",
) -> str:
    """Bundle YAML; ``l0_extra`` is YAML placed inside the ``l0`` block."""

    base = textwrap.dedent(
        f"""\
        contract_version: "0.4"
        id: {bundle_id}
        version: {version}
        mode: {mode}
        phases: [PRE_LLM, TOOL_INPUT]
        on_evaluation_failure: {failure}
        l0:
          entities: [{", ".join(entities or CORE)}]
        """
    )
    extra = textwrap.indent(textwrap.dedent(l0_extra).strip("\n"), "  ") + "\n" if l0_extra else ""
    return base + extra + f"l1a:\n  rules: {l1a_rules}\n"


def custom_recognizer_bundle_yaml(pattern: str, **kwargs) -> str:
    """A 0.5 bundle whose only entity is the custom CUSTOMER_NO with one pattern, ``greedy``."""

    custom = f"""\
        custom_recognizers:
          - entity: CUSTOMER_NO
            patterns: [{{name: greedy, regex: '{pattern}', score: 0.9}}]
        """
    return bundle_yaml(entities=["CUSTOMER_NO"], l0_extra=custom, **kwargs).replace(
        'contract_version: "0.4"', 'contract_version: "0.5"', 1
    )


@pytest.fixture
def write_bundle(tmp_path: Path) -> Callable[..., Path]:
    root = tmp_path / "policies"

    def _write(
        content: str,
        bundle_id: str = "gr-test",
        version: int = 1,
        *,
        active: int | None = None,
        manifest: str | None = None,
        tenant: str | None = None,
    ) -> Path:
        # tenant: owned by that tenant (policies/tenants/<tenant>/..., policy-bundle 0.8)
        base = root if tenant is None else root / "tenants" / tenant
        directory = base / bundle_id / str(version)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "bundle.yaml").write_bytes(content.encode("utf-8"))  # exact bytes: hashes
        if manifest is not None:
            (directory / "manifest.json").write_bytes(manifest.encode("utf-8"))
        if active is not None:
            (base / bundle_id / "active").write_text(str(active), encoding="utf-8")
        return directory

    _write.root = root  # type: ignore[attr-defined]
    return _write


@pytest.fixture(scope="session")
def detector() -> EntityDetector:
    d = EntityDetector(lambda tenant: f"key-{tenant}".encode(), threads=2)
    yield d
    d.close()


@pytest.fixture
def core_config() -> L0Config:
    return L0Config(entities=CORE)
