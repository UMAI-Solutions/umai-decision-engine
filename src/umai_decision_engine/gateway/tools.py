"""Agent traffic (gateway WP5): tool results going to the model, tool calls coming back.

- **Tool results** (``tool`` / ``function`` messages): when the route's guardrail enables
  ``TOOL_OUTPUT``, each one is checked on its own as ``TOOL_OUTPUT`` with the tool's name as
  ``action.tool_name`` (policies with the ``tool`` role, exceptions on the tool name). Otherwise
  they are part of the ``PRE_LLM`` input check. Either way they are masked before they leave.
- **Tool calls in the answer**: after the caller's values are restored, each call's arguments
  are checked as ``TOOL_INPUT`` (policies with the ``tool_call`` role) when the guardrail enables
  it: masked in place, or the answer is refused.

All checks of a call share one placeholder namespace (contract gateway-api).
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
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


def _tool_action(message: dict[str, Any], names: dict[str, str]) -> AgentAction | None:
    if message.get("role") == "function":
        name = message.get("name")
    else:
        name = names.get(str(message.get("tool_call_id")))
    if not isinstance(name, str) or not name:
        return None
    return AgentAction(name=name[:128], tool_name=name[:128])


async def input_checks(
    call: Call, messages: list[Any], segments: list[Segment]
) -> list[tuple[Check, list[Segment]]]:
    """Run the input check and, per tool result, the tool-result checks, concurrently."""

    separate = any(s.tool_result for s in segments) and call.guard.enabled(
        call.ctx, Phase.TOOL_OUTPUT
    )
    general = [s for s in segments if not (separate and s.tool_result)]
    groups: list[tuple[Phase, list[Segment], AgentAction | None]] = []
    if general:
        groups.append((Phase.PRE_LLM, general, None))
    if separate:
        names = tool_names(messages)
        by_message: OrderedDict[int, list[Segment]] = OrderedDict()
        for segment in segments:
            if segment.tool_result:
                by_message.setdefault(segment.message, []).append(segment)
        for index, parts in by_message.items():
            groups.append((Phase.TOOL_OUTPUT, parts, _tool_action(messages[index], names)))
    checks = await asyncio.gather(
        *(
            call.guard.check(call.ctx, phase, [(s.role, s.text) for s in parts], action=action)
            for phase, parts, action in groups
        )
    )
    return [(check, parts) for check, (_, parts, _) in zip(checks, groups, strict=True)]


async def check_tool_calls(call: Call, functions: list[dict[str, Any]]) -> Check | None:
    """Check each tool call (``{"name", "arguments"}``, arguments already restored) as
    ``TOOL_INPUT``; mask arguments in place. Returns the first blocking check, if any."""

    if not functions or not call.guard.enabled(call.ctx, Phase.TOOL_INPUT):
        return None
    pending = [f for f in functions if isinstance(f.get("arguments"), str) and f["arguments"]]
    checks = await asyncio.gather(
        *(
            call.guard.check(
                call.ctx,
                Phase.TOOL_INPUT,
                [("assistant", f["arguments"])],
                action=AgentAction(
                    name=str(f.get("name") or "")[:128] or None,
                    tool_name=str(f.get("name") or "")[:128] or None,
                ),
            )
            for f in pending
        )
    )
    blocking: Check | None = None
    for function, check in zip(pending, checks, strict=True):
        note_output(call, check)
        if not check.allowed:
            blocking = blocking or check
        elif check.masks:
            function["arguments"] = call.session.mask(
                function["arguments"], check.spans[0], from_input=False
            )
    return blocking
