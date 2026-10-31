"""The evaluation request accepts every version the contract document names (gap found by the
API documentation, 2026-10-31: 0.13 and 0.14 were refused with 422)."""

from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from umai_decision_engine.contracts.evaluation import CONTRACT_VERSION, EvaluationRequest

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "evaluation-api.md"


def _accepted() -> tuple[str, ...]:
    return typing.get_args(EvaluationRequest.model_fields["contract_version"].annotation)


def _request(version: str) -> dict:
    return {
        "contract_version": version,
        "request_id": "req-1",
        "timestamp": "2026-10-31T09:30:00Z",
        "phase": "PRE_LLM",
        "content": {
            "messages": [{"role": "user", "content": "Merhaba"}],
            "focus": "LAST_USER_MESSAGE",
        },
    }


def test_the_contract_documents_version_is_accepted() -> None:
    documented = re.search(r"\*\*Version:\*\* (\d+\.\d+)", CONTRACT.read_text(encoding="utf-8"))
    assert documented
    assert documented.group(1) in _accepted()
    assert CONTRACT_VERSION in _accepted()


@pytest.mark.parametrize("version", ["0.12", "0.13", "0.14"])
def test_requests_name_any_version_since_the_last_shape_change(version: str) -> None:
    assert EvaluationRequest.model_validate(_request(version)).contract_version == version


def test_an_unknown_version_is_refused() -> None:
    with pytest.raises(ValidationError):
        EvaluationRequest.model_validate(_request("0.99"))
