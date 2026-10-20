"""Per-call placeholders and their restoration (ADR-0035, contract gateway-api).

One placeholder namespace per call: the same value (same type and tenant HMAC) gets the same
``<TYPE_n>`` everywhere in the call, different values never share one, and numbers start above
any placeholder the call's input already shows (an earlier answer's, the caller's own), so one
placeholder never means two values in the model's context. The map from placeholder to value
lives in this object only, for one call, in memory; it is never logged or stored. Restoration
is exact-match only and only for placeholders created from the caller's input.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable

_PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9_]*_\d+>")
# Placeholders already in the input: type and number (longer numbers are not ours).
_NUMBERED = re.compile(r"<([A-Z][A-Z0-9_]*)_([0-9]{1,12})>")
# Longest placeholder we may have to wait for while streaming: "<" + type + "_" + number + ">".
MAX_PLACEHOLDER_CHARS = 64
_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?")

Span = tuple[int, int, str, str]  # start, end, entity type, value hash (within one text)


class MaskSession:
    def __init__(self, *, base: int = 0) -> None:
        """``base``: every type's numbering starts above it (calls whose earlier turns the
        provider stores, where the input cannot show their placeholders)."""

        self._by_key: dict[tuple[str, str], str] = {}
        self._base = base
        self._counters: dict[str, int] = {}
        self._values: dict[str, str] = {}  # placeholder → caller's value (input only)

    @classmethod
    def for_call(cls, texts: Iterable[str], *, stored_history: bool = False) -> MaskSession:
        """A call's session: numbering starts above the placeholders in the call's input and,
        with ``stored_history`` (earlier turns the provider keeps, which the input cannot
        show), above the current time in milliseconds, which grows from turn to turn."""

        session = cls(base=int(time.time() * 1000) % 10**9 if stored_history else 0)
        session.reserve(texts)
        return session

    def reserve(self, texts: Iterable[str]) -> None:
        """Number new placeholders above every ``<TYPE_n>`` already present in ``texts``."""

        for text in texts:
            if "<" not in text:
                continue
            for match in _NUMBERED.finditer(text):
                entity_type, n = match.group(1), int(match.group(2))
                if n > self._counters.get(entity_type, 0):
                    self._counters[entity_type] = n

    def placeholder(
        self, entity_type: str, value_hash: str, value: str, *, from_input: bool
    ) -> str:
        key = (entity_type, value_hash)
        token = self._by_key.get(key)
        if token is None:
            n = max(self._counters.get(entity_type, 0), self._base) + 1
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

    def mask_text(self, text: str, spans: list[Span], *, from_input: bool, json_text: bool) -> str:
        return (
            self.mask_json(text, spans, from_input=from_input)
            if json_text
            else self.mask(text, spans, from_input=from_input)
        )

    def mask_json(self, text: str, spans: list[Span], *, from_input: bool) -> str:
        """Mask JSON source (tool-call arguments) value by value, so it stays valid JSON: a span
        inside a string is replaced inside that string; a number holding a span becomes a string
        (``{"tckn": 10000000146}`` → ``{"tckn": "<TR_TCKN_1>"}``). Text that is not JSON is
        masked as text."""

        tokens = _json_tokens(text)
        if tokens is None:
            return self.mask(text, spans, from_input=from_input)
        spans = sorted(spans)
        # A span over several values (rare) masks each piece; none of them is restorable.
        values_hit = [sum(1 for t in tokens if t[0] < e and s < t[1]) for s, e, _, _ in spans]
        out: list[str] = []
        cursor = 0
        for start, end, is_string in tokens:
            edits = []
            for (s, e, entity_type, value_hash), hit in zip(spans, values_hit, strict=True):
                lo, hi = (start + 1, end - 1) if is_string else (start, end)
                a, b = max(s, lo), min(e, hi)
                if a >= b:
                    continue
                if is_string:
                    a, b = _outside_escapes(text, lo, a, b)
                if edits and a < edits[-1][1]:
                    continue  # overlaps the previous span: that one already covers it
                piece = text[a:b]
                value = json.loads(f'"{piece}"') if is_string else piece
                token = self.placeholder(
                    entity_type, value_hash, value, from_input=from_input and hit == 1
                )
                edits.append((a, b, token))
            if not edits:
                continue
            rendered: list[str] = []
            position = start
            for a, b, token in edits:
                rendered += [text[position:a], token]
                position = b
            rendered.append(text[position:end])
            value_text = "".join(rendered)
            out += [text[cursor:start], value_text if is_string else f'"{value_text}"']
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


def _reject_constant(name: str) -> None:
    raise ValueError(name)  # NaN, Infinity: not JSON, masked as text


def _json_tokens(text: str) -> list[tuple[int, int, bool]] | None:
    """``(start, end, is_string)`` of every string and number of a JSON document (a string's
    range includes its quotes), or ``None`` when ``text`` is not JSON."""

    try:
        json.loads(text, parse_constant=_reject_constant)
    except ValueError:
        return None
    tokens: list[tuple[int, int, bool]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            tokens.append((i, j + 1, True))
            i = j + 1
        elif ch == "-" or "0" <= ch <= "9":
            match = _JSON_NUMBER.match(text, i)
            assert match is not None  # the text parsed as JSON
            tokens.append((i, match.end(), False))
            i = match.end()
        else:
            i += 1  # structure, whitespace, true / false / null
    return tokens


def _outside_escapes(text: str, lo: int, a: int, b: int) -> tuple[int, int]:
    """Widen ``[a, b)`` inside a JSON string starting at ``lo`` so no escape is cut in half."""

    i = lo
    while i < b:
        if text[i] != "\\":
            i += 1
            continue
        length = 6 if text[i + 1] == "u" else 2
        if i < a < i + length:
            a = i
        if i < b < i + length:
            b = i + length
        i += length
    return a, b
