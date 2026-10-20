"""Fields the guardrail cannot check (gateway-api 0.11).

The gateway forwards caller content only after the guardrail has scanned it, and returns an
answer only as text it has checked. A request field that would carry caller content past the
scan is refused or left out; an answer field that repeats the answer outside the checked text
(log probabilities, reasoning text) is not requested and is removed.
"""

from __future__ import annotations

from typing import Any

from umai_decision_engine.gateway.errors import GatewayHTTPError

# Message roles whose optional ``name`` is a participant name (``function`` and ``tool`` use
# the field for the function, which the checks see).
_PARTICIPANT_ROLES = frozenset({"system", "developer", "user", "assistant"})
# Answer text outside ``content`` that some servers return (vLLM, OpenAI-compatible reasoning).
_UNCHECKED_ANSWER_TEXT = ("reasoning_content", "reasoning")


def _refuse(param: str, what: str) -> GatewayHTTPError:
    return GatewayHTTPError(
        400, "unsupported_content", f"{what} cannot be checked by the guardrail", param=param
    )


def check_chat_request(body: dict[str, Any]) -> None:
    """Refuse Chat Completions features whose content the guardrail cannot check."""

    if body.get("prediction") is not None:
        raise _refuse("prediction", "predicted output content")
    modalities = body.get("modalities")
    if body.get("audio") is not None or (isinstance(modalities, list) and "audio" in modalities):
        raise _refuse("modalities", "an audio answer")


def clean_chat_upstream(body: dict[str, Any]) -> None:
    """Leave out what would reach the provider unscanned or come back unchecked."""

    for key in ("metadata", "logprobs", "top_logprobs"):
        body.pop(key, None)
    for message in body.get("messages") or []:
        if isinstance(message, dict) and message.get("role") in _PARTICIPANT_ROLES:
            message.pop("name", None)


def clean_chat_answer(data: dict[str, Any]) -> None:
    """Remove answer fields that repeat the answer outside the checked ``content``."""

    for choice in data.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        if "logprobs" in choice:
            choice["logprobs"] = None
        for key in ("message", "delta"):
            part = choice.get(key)
            if isinstance(part, dict):
                for field in _UNCHECKED_ANSWER_TEXT:
                    part.pop(field, None)


def unchecked_delta_field(name: str) -> bool:
    """A streamed delta field that carries answer text outside ``content``."""

    return name in _UNCHECKED_ANSWER_TEXT


def check_responses_request(body: dict[str, Any]) -> None:
    """Refuse Responses API features whose content the guardrail cannot check."""

    prompt = body.get("prompt")
    if isinstance(prompt, dict) and prompt.get("variables"):
        raise _refuse("prompt.variables", "stored-prompt variables")


def clean_responses_upstream(body: dict[str, Any]) -> None:
    """Leave out metadata and log probabilities, and do not ask for reasoning summaries (text
    the answer checks do not see)."""

    for key in ("metadata", "top_logprobs"):
        body.pop(key, None)
    include = body.get("include")
    if isinstance(include, list):
        kept = [i for i in include if not (isinstance(i, str) and "logprobs" in i)]
        if kept:
            body["include"] = kept
        else:
            body.pop("include")
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        reasoning.pop("summary", None)
        reasoning.pop("generate_summary", None)


def clean_responses_answer(data: dict[str, Any]) -> None:
    """Remove log probabilities and reasoning summaries from a Responses answer."""

    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "reasoning" and item.get("summary"):
            item["summary"] = []
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("logprobs"):
                part["logprobs"] = []
