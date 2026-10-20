"""Agent traffic (gateway WP5): tool results going to the model, tool calls coming back.

- **Tool results** (``tool`` / ``function`` messages): when the route's guardrail enables
  ``TOOL_OUTPUT``, each one is checked on its own as ``TOOL_OUTPUT`` with the tool's name as
  ``action.tool_name`` (policies with the ``tool`` role, exceptions on the tool name). Otherwise
  they are part of the ``PRE_LLM`` input check. Either way they are masked before they leave.
- **Tool calls in the answer**: after the caller's values are restored, each call's input is
  checked as ``TOOL_INPUT`` (policies with the ``tool_call`` role) when the guardrail enables
  it: masked in place, or the answer is refused. Every shape counts (gateway-api 0.12): Chat
  ``function`` and ``custom`` tool calls and the deprecated ``function_call``; Responses
  function, custom, MCP, shell, apply-patch and computer calls. JSON input is masked value by
  value, so it stays valid JSON.

All checks of a call share one placeholder namespace (contract gateway-api).
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from umai_decision_engine.contracts.common import ACTION_RANK, Phase
from umai_decision_engine.contracts.evaluation import AgentAction
from umai_decision_engine.gateway.content import Segment
from umai_decision_engine.gateway.guard import Check

if TYPE_CHECKING:
    from umai_decision_engine.gateway.chat import Call


def note_output(call: Call, check: Check) -> None:
    """Remember the strictest answer-side action for the response header and the log."""

    action = check.decision.action
    if call.output_action is None or ACTION_RANK[action] > ACTION_RANK[call.output_action]:
        call.output_action = action


def tool_names(messages: list[Any]) -> dict[str, str]:
    """``tool_call_id`` → function name, from the assistant tool calls in the history."""

    names: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if (
                isinstance(tool_call.get("id"), str)
                and isinstance(function, dict)
                and isinstance(function.get("name"), str)
            ):
                names[tool_call["id"]] = function["name"]
    return names


def tool_action(name: Any) -> AgentAction | None:
    if not isinstance(name, str) or not name:
        return None
    return AgentAction(name=name[:128], tool_name=name[:128])


def chat_tool_action(messages: list[Any]) -> Callable[[int], AgentAction | None]:
    """For Chat Completions: the tool behind ``messages[i]`` (a ``tool`` / ``function`` message)."""

    names = tool_names(messages)

    def _action(index: int) -> AgentAction | None:
        message = messages[index]
        if message.get("role") == "function":
            return tool_action(message.get("name"))
        return tool_action(names.get(str(message.get("tool_call_id"))))

    return _action


async def input_checks(
    call: Call,
    segments: list[Segment],
    action_for: Callable[[int], AgentAction | None],
) -> list[tuple[Check, list[Segment]]]:
    """Run the input check and, per tool result, the tool-result checks, concurrently.
    ``action_for(segment.message)`` names the tool behind a tool result."""

    separate = any(s.tool_result for s in segments) and call.guard.enabled(
        call.ctx, Phase.TOOL_OUTPUT
    )
    general = [s for s in segments if not (separate and s.tool_result)]
    groups: list[tuple[Phase, list[Segment], AgentAction | None]] = []
    if general:
        groups.append((Phase.PRE_LLM, general, None))
    if separate:
        by_message: OrderedDict[int, list[Segment]] = OrderedDict()
        for segment in segments:
            if segment.tool_result:
                by_message.setdefault(segment.message, []).append(segment)
        for index, parts in by_message.items():
            groups.append((Phase.TOOL_OUTPUT, parts, action_for(index)))
    checks = await asyncio.gather(
        *(
            call.guard.check(call.ctx, phase, [(s.role, s.text) for s in parts], action=action)
            for phase, parts, action in groups
        )
    )
    return [(check, parts) for check, (_, parts, _) in zip(checks, groups, strict=True)]


@dataclass
class ToolCall:
    """One tool call in an answer: the tool's name and the text ``TOOL_INPUT`` reads, which
    ``write`` puts back in place after restoration and masking."""

    name: str | None
    text: str
    json_text: bool  # JSON source: restored JSON-escaped, masked value by value
    write: Callable[[str], None]
    # Only where the input side scans and masks the same field again on the next turn: a value
    # restored anywhere else would go back to the provider raw (ADR-0005, review of PR #12).
    restorable: bool = True

    def update(self, text: str) -> None:
        self.text = text
        self.write(text)


def _put(holder: dict[str, Any], key: str) -> Callable[[str], None]:
    def _write(text: str) -> None:
        holder[key] = text

    return _write


def _put_json(holder: dict[str, Any], key: str) -> Callable[[str], None]:
    def _write(text: str) -> None:
        holder[key] = json.loads(text)  # restoration and mask_json keep it valid JSON

    return _write


def _name(value: Any) -> str | None:
    return value[:128] if isinstance(value, str) and value else None


def _structured(name: str, holder: dict[str, Any], key: str) -> ToolCall:
    """A tool call whose input is a JSON structure (shell commands, a patch): checked as its
    JSON text, written back as the masked structure."""

    text = json.dumps(holder[key], ensure_ascii=False)
    return ToolCall(name, text, True, _put_json(holder, key), restorable=False)


def chat_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    """The tool calls of a Chat Completions answer message: ``function`` and ``custom`` tool
    calls, any other typed call by its JSON payload, and the deprecated ``function_call``."""

    calls: list[ToolCall] = []
    tool_calls = message.get("tool_calls")
    for tool_call in tool_calls if isinstance(tool_calls, list) else []:
        if not isinstance(tool_call, dict):
            continue
        kind = tool_call.get("type") or "function"
        payload = tool_call.get(kind)
        if not isinstance(payload, dict):
            continue
        if kind == "function" and isinstance(payload.get("arguments"), str):
            calls.append(_text_call(payload, "arguments", json_text=True))
        elif kind == "custom" and isinstance(payload.get("input"), str):
            calls.append(_text_call(payload, "input", json_text=False))
        elif kind not in ("function", "custom"):
            calls.append(_structured(_name(payload.get("name")) or kind, tool_call, kind))
    legacy = message.get("function_call")
    if isinstance(legacy, dict) and isinstance(legacy.get("arguments"), str):
        calls.append(_text_call(legacy, "arguments", json_text=True))
    return calls


def _text_call(holder: dict[str, Any], key: str, *, json_text: bool) -> ToolCall:
    return ToolCall(_name(holder.get("name")), holder[key], json_text, _put(holder, key))


# Responses output items the caller's client (or the provider, for MCP) acts on: where their
# input lives. The model's text items and provider-side items are handled elsewhere.
_RESPONSES_TEXT_INPUT = {
    "function_call": ("arguments", True),
    "custom_tool_call": ("input", False),
    "mcp_call": ("arguments", True),
    "mcp_approval_request": ("arguments", True),
}
# The items whose input the request side scans again (responses_segments): only these get the
# caller's values back.
_RESPONSES_RESCANNED = frozenset({"function_call", "custom_tool_call"})
_RESPONSES_STRUCTURED_INPUT = {
    "shell_call": ("action",),
    "local_shell_call": ("action",),
    "apply_patch_call": ("operation",),
    "computer_call": ("action", "actions"),
}
RESPONSES_TOOL_ITEMS = frozenset(_RESPONSES_TEXT_INPUT) | frozenset(_RESPONSES_STRUCTURED_INPUT)


def responses_tool_fields(kind: Any) -> tuple[str, ...]:
    """The fields holding a Responses tool item's input."""

    if kind in _RESPONSES_TEXT_INPUT:
        return (_RESPONSES_TEXT_INPUT[kind][0],)
    return _RESPONSES_STRUCTURED_INPUT.get(kind, ())


def responses_tool_calls(item: dict[str, Any]) -> list[ToolCall]:
    """The tool call(s) of one Responses output item; empty for any other item."""

    kind = item.get("type")
    if kind in _RESPONSES_TEXT_INPUT:
        key, json_text = _RESPONSES_TEXT_INPUT[kind]
        if not isinstance(item.get(key), str):
            return []
        tool_call = _text_call(item, key, json_text=json_text)
        tool_call.restorable = kind in _RESPONSES_RESCANNED
        return [tool_call]
    if kind in _RESPONSES_STRUCTURED_INPUT:
        name = kind.removesuffix("_call")
        return [
            _structured(name, item, key)
            for key in _RESPONSES_STRUCTURED_INPUT[kind]
            if item.get(key) is not None
        ]
    return []


def restore_tool_calls(call: Call, calls: list[ToolCall]) -> None:
    """Put the caller's own values back into each call (before its check, ADR-0036)."""

    if not call.route.restore_placeholders:
        return
    for tool_call in calls:
        if not tool_call.restorable:
            continue
        restored = call.session.restore(tool_call.text, json_string=tool_call.json_text)
        if restored != tool_call.text:
            tool_call.update(restored)


async def check_tool_calls(call: Call, calls: list[ToolCall]) -> Check | None:
    """Check each tool call (already restored) as ``TOOL_INPUT``; mask its input in place.
    Returns the first blocking check, if any."""

    if not calls or not call.guard.enabled(call.ctx, Phase.TOOL_INPUT):
        return None
    pending = [c for c in calls if c.text]
    checks = await asyncio.gather(
        *(
            call.guard.check(
                call.ctx,
                Phase.TOOL_INPUT,
                [("assistant", c.text)],
                action=AgentAction(name=c.name, tool_name=c.name),
            )
            for c in pending
        )
    )
    blocking: Check | None = None
    for tool_call, check in zip(pending, checks, strict=True):
        note_output(call, check)
        if not check.allowed:
            blocking = blocking or check
        elif check.masks:
            tool_call.update(
                call.session.mask_text(
                    tool_call.text, check.spans[0], from_input=False, json_text=tool_call.json_text
                )
            )
    return blocking


async def settle_tool_calls(call: Call, calls: list[ToolCall]) -> Check | None:
    """Restore, then check: what every tool call in an answer goes through."""

    restore_tool_calls(call, calls)
    return await check_tool_calls(call, calls)
