"""Per-call placeholders and their restoration (ADR-0035, contract gateway-api).

One placeholder namespace per call: the same value (same type and tenant HMAC) gets the same
``<TYPE_n>`` everywhere in the call, different values never share one. The map from placeholder
to value lives in this object only, for one call, in memory; it is never logged or stored.
Restoration is exact-match only and only for placeholders created from the caller's input.
"""

from __future__ import annotations

import json
import re

_PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9_]*_\d+>")
# Longest placeholder we may have to wait for while streaming: "<" + type + "_" + number + ">".
MAX_PLACEHOLDER_CHARS = 64

Span = tuple[int, int, str, str]  # start, end, entity type, value hash (within one text)


class MaskSession:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}
        self._values: dict[str, str] = {}  # placeholder → caller's value (input only)

    def placeholder(
        self, entity_type: str, value_hash: str, value: str, *, from_input: bool
    ) -> str:
        key = (entity_type, value_hash)
        token = self._by_key.get(key)
        if token is None:
            n = self._counters.get(entity_type, 0) + 1
            self._counters[entity_type] = n
            token = f"<{entity_type}_{n}>"
            self._by_key[key] = token
        if from_input and token not in self._values:
            self._values[token] = value
        return token

    def mask(self, text: str, spans: list[Span], *, from_input: bool) -> str:
        """``text`` with every span replaced by its call-wide placeholder."""

        out: list[str] = []
        cursor = 0
        for start, end, entity_type, value_hash in sorted(spans):
            if start < cursor or end > len(text) or start >= end:
                continue  # overlapping or out of range: the earlier span already covers it
            out.append(text[cursor:start])
            out.append(
                self.placeholder(entity_type, value_hash, text[start:end], from_input=from_input)
            )
            cursor = end
        out.append(text[cursor:])
        return "".join(out)

    @property
    def restorable(self) -> bool:
        return bool(self._values)

    def restore(self, text: str, *, json_string: bool = False) -> str:
        """Put the caller's own values back where the model kept a placeholder intact.
        ``json_string``: ``text`` is JSON source (tool-call arguments), so values are escaped."""

        if not self._values or "<" not in text:
            return text

        def _sub(match: re.Match[str]) -> str:
            value = self._values.get(match.group(0))
            if value is None:
                return match.group(0)
            return json.dumps(value, ensure_ascii=False)[1:-1] if json_string else value

        return _PLACEHOLDER.sub(_sub, text)

    @staticmethod
    def unfinished_tail(text: str) -> int:
        """Length of a trailing ``<…`` that could still become a placeholder (streaming)."""

        start = text.rfind("<", max(0, len(text) - MAX_PLACEHOLDER_CHARS))
        if start == -1 or ">" in text[start:]:
            return 0
        tail = text[start:]
        return len(tail) if re.fullmatch(r"<[A-Z0-9_]*", tail) else 0
