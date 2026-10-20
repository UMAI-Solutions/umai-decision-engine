"""Select the text L0 scans (``l0.target``, same values as ``umai-engine``)."""

from __future__ import annotations

from umai_decision_engine.contracts.bundle import TargetScope
from umai_decision_engine.contracts.evaluation import ChatMessage, Content

SEPARATOR = "\n\n"


def _focus_message(content: Content) -> ChatMessage:
    role = "user" if content.focus == "LAST_USER_MESSAGE" else "assistant"
    for message in reversed(content.messages):
        if message.role == role:
            return message
    return content.messages[-1]


def _attachments(content: Content) -> list[str]:
    parts: list[str] = []
    for artifact in content.artifacts:
        for value in (artifact.payload_summary, artifact.content):
            if value:
                parts.append(value)
    return parts


def extract_target_parts(content: Content, target: TargetScope) -> list[tuple[str, str]]:
    """``(role, text)`` of every part L0 scans, in order. Attachment parts have role ``tool``.
    L1b judges these same parts, redacted (ADR-0040): nothing L0 did not scan leaves it."""

    messages = [(m.role, m.content) for m in content.messages]
    attachments = [("tool", text) for text in _attachments(content)]
    if target == "LAST_MESSAGE":
        focus = _focus_message(content)
        return [(focus.role, focus.content)]
    if target == "FULL_HISTORY":
        return messages
    if target == "ATTACHMENTS":
        return attachments
    return [*messages, *attachments]


def extract_target_segments(content: Content, target: TargetScope) -> list[str]:
    """The texts L0 scans, one per message or attachment part. L0 scans each on its own
    (ADR-0037) and reports spans in ``SEPARATOR.join(segments)``."""

    return [text for _, text in extract_target_parts(content, target)]


def extract_target_text(content: Content, target: TargetScope) -> str:
    return SEPARATOR.join(extract_target_segments(content, target))
