"""Select the text L0 scans (``l0.target``, same values as ``umai-engine``)."""

from __future__ import annotations

from umai_decision_engine.contracts.bundle import TargetScope
from umai_decision_engine.contracts.evaluation import Content

SEPARATOR = "\n\n"


def _focus_message(content: Content) -> str:
    role = "user" if content.focus == "LAST_USER_MESSAGE" else "assistant"
    for message in reversed(content.messages):
        if message.role == role:
            return message.content
    return content.messages[-1].content


def _attachments(content: Content) -> list[str]:
    parts: list[str] = []
    for artifact in content.artifacts:
        for value in (artifact.payload_summary, artifact.content):
            if value:
                parts.append(value)
    return parts


def extract_target_segments(content: Content, target: TargetScope) -> list[str]:
    """The texts L0 scans, one per message or attachment part. L0 scans each on its own
    (ADR-0037) and reports spans in ``SEPARATOR.join(segments)``."""

    if target == "LAST_MESSAGE":
        return [_focus_message(content)]
    if target == "FULL_HISTORY":
        return [m.content for m in content.messages]
    if target == "ATTACHMENTS":
        return _attachments(content)
    return [*(m.content for m in content.messages), *_attachments(content)]


def extract_target_text(content: Content, target: TargetScope) -> str:
    return SEPARATOR.join(extract_target_segments(content, target))
