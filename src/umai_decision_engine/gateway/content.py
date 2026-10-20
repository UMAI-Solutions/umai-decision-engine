"""Where the scannable text of a Chat Completions request lives, and how to put masked text back.

A *segment* is one piece of text the gateway scans: a string message content, a ``text`` part
of a content array, or the ``arguments`` of a tool call in the history. Everything else in the
body passes through untouched (contract gateway-api, Request fields the gateway reads).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from umai_decision_engine.l0.target import SEPARATOR

Path = tuple[str | int, ...]

# Provider roles → the roles an evaluation knows. Tool results and the deprecated "function"
# role are input to the model, like user content.
_EVAL_ROLE = {
    "system": "system",
    "developer": "system",
    "user": "user",
    "assistant": "assistant",
    "tool": "user",
    "function": "user",
}


class ContentError(ValueError):
    """The body is not a Chat Completions request the gateway can read."""


@dataclass(frozen=True)
class Segment:
    message: int  # index in messages[]
    path: Path  # path inside that message
    role: str  # evaluation role: system | user | assistant
    text: str
    tool_result: bool = False  # content of a "tool" message (TOOL_OUTPUT, WP5)
    json_string: bool = False  # text is a JSON-encoded string (tool-call arguments)


def input_segments(messages: Any) -> tuple[list[Segment], int]:
    """Segments of ``messages`` and the number of parts the gateway cannot scan."""

    if not isinstance(messages, list) or not messages:
        raise ContentError("messages must be a non-empty array")
    segments: list[Segment] = []
    unscanned = 0
    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ContentError(f"messages[{i}] must be an object")
        role = message.get("role")
        if role not in _EVAL_ROLE:
            raise ContentError(f"messages[{i}].role is not supported")
        eval_role = _EVAL_ROLE[role]
        tool_result = role in ("tool", "function")
        found, skipped = _content_segments(message.get("content"), i, eval_role, tool_result)
        segments.extend(found)
        unscanned += skipped
        if role == "assistant":
            segments.extend(_tool_call_segments(message, i))
    return segments, unscanned


def _content_segments(
    content: Any, index: int, role: str, tool_result: bool
) -> tuple[list[Segment], int]:
    if content is None:
        return [], 0
    if isinstance(content, str):
        return [Segment(index, ("content",), role, content, tool_result)], 0
    if not isinstance(content, list):
        raise ContentError(f"messages[{index}].content must be a string or an array")
    found: list[Segment] = []
    unscanned = 0
    for j, part in enumerate(content):
        if not isinstance(part, dict):
            raise ContentError(f"messages[{index}].content[{j}] must be an object")
        kind = part.get("type")
        if kind == "text" and isinstance(part.get("text"), str):
            found.append(Segment(index, ("content", j, "text"), role, part["text"], tool_result))
        elif kind == "refusal" and isinstance(part.get("refusal"), str):
            found.append(Segment(index, ("content", j, "refusal"), role, part["refusal"]))
        else:
            unscanned += 1  # image, audio, file: not scannable as text
    return found, unscanned


def _tool_call_segments(message: dict[str, Any], index: int) -> list[Segment]:
    found: list[Segment] = []
    calls = message.get("tool_calls")
    if isinstance(calls, list):
        for k, call in enumerate(calls):
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                found.append(
                    Segment(
                        index,
                        ("tool_calls", k, "function", "arguments"),
                        "assistant",
                        function["arguments"],
                        json_string=True,
                    )
                )
    legacy = message.get("function_call")
    if isinstance(legacy, dict) and isinstance(legacy.get("arguments"), str):
        found.append(
            Segment(
                index,
                ("function_call", "arguments"),
                "assistant",
                legacy["arguments"],
                json_string=True,
            )
        )
    return found


def set_text(messages: list[Any], segment: Segment, text: str) -> None:
    """Write ``text`` where ``segment`` came from (``messages`` is the caller's deep copy)."""

    target: Any = messages[segment.message]
    for key in segment.path[:-1]:
        target = target[key]
    target[segment.path[-1]] = text


def segment_offsets(texts: list[str]) -> list[int]:
    """Start of each text in ``SEPARATOR.join(texts)`` (how the pipeline joins messages)."""

    starts: list[int] = []
    cursor = 0
    for text in texts:
        starts.append(cursor)
        cursor += len(text) + len(SEPARATOR)
    return starts


def text_size(segments: list[Segment]) -> int:
    return sum(len(s.text) for s in segments)
