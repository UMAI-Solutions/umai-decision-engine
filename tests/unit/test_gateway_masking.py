"""Gateway placeholders and event-stream lines (gateway-api 0.12 review fixes). Synthetic data."""

from __future__ import annotations

import json

import httpx

from umai_decision_engine.gateway.masking import MaskSession
from umai_decision_engine.gateway.streaming import sse_events, sse_lines

TCKN = "10000000146"
BACKSLASH = chr(92)


def _span(text: str, value: str, entity_type: str) -> tuple[int, int, str, str]:
    start = text.index(value)
    return (start, start + len(value), entity_type, f"h-{value}")


# ------------------------------------------------------------------ JSON arguments


def test_a_number_holding_an_entity_becomes_a_string_and_stays_json() -> None:
    session = MaskSession()
    arguments = json.dumps({"tckn": int(TCKN), "note": f"id {TCKN}"})
    masked = session.mask_json(arguments, [_span(arguments, TCKN, "TR_TCKN")], from_input=True)
    assert json.loads(masked)["tckn"] == "<TR_TCKN_1>"
    # The value inside the string is masked too: same value, same placeholder.
    note_span = arguments.index(TCKN, arguments.index("note"))
    masked = session.mask_json(
        arguments,
        [(note_span, note_span + len(TCKN), "TR_TCKN", f"h-{TCKN}")],
        from_input=True,
    )
    assert json.loads(masked) == {"tckn": int(TCKN), "note": "id <TR_TCKN_1>"}
    restored = session.restore(json.dumps({"tckn": "<TR_TCKN_1>"}), json_string=True)
    assert json.loads(restored) == {"tckn": TCKN}


def test_escapes_are_never_cut_and_quotes_stay() -> None:
    session = MaskSession()
    escaped = f"Ay{BACKSLASH}u015fe Demir"  # "Ayşe Demir" as JSON source
    arguments = '{"name": "' + escaped + '", "city": "Ankara"}'
    start = arguments.index("u015f")  # a span starting inside the escape
    masked = session.mask_json(arguments, [(start, start + 13, "PERSON", "h-p")], from_input=True)
    assert json.loads(masked) == {"name": "Ay<PERSON_1>", "city": "Ankara"}
    # Spans that include the quotes keep them.
    email = '{"to": "ayse.demir@example.com"}'
    masked = session.mask_json(email, [(7, 31, "EMAIL_ADDRESS", "h-e")], from_input=True)
    assert json.loads(masked) == {"to": "<EMAIL_ADDRESS_1>"}


def test_text_that_is_not_json_is_masked_as_text() -> None:
    session = MaskSession()
    text = f"tckn={TCKN}"
    assert session.mask_json(text, [_span(text, TCKN, "TR_TCKN")], from_input=False) == (
        "tckn=<TR_TCKN_1>"
    )


# ------------------------------------------------------------------ numbering


def test_numbers_start_above_placeholders_already_in_the_input() -> None:
    session = MaskSession.for_call(["earlier: <EMAIL_ADDRESS_1> and <TR_TCKN_4>", "plain"])
    assert session.placeholder("EMAIL_ADDRESS", "h1", "a@example.com", from_input=True) == (
        "<EMAIL_ADDRESS_2>"
    )
    assert session.placeholder("TR_TCKN", "h2", TCKN, from_input=True) == "<TR_TCKN_5>"
    assert session.placeholder("PERSON", "h3", "Ayşe", from_input=True) == "<PERSON_1>"


def test_stored_history_numbers_grow_from_turn_to_turn(monkeypatch) -> None:
    """``previous_response_id``: the earlier turn's placeholders live at the provider. The next
    turn starts after the earlier one answered, so its numbers start higher."""

    clock = iter([1_792_442_629.000, 1_792_442_630.500])
    monkeypatch.setattr("umai_decision_engine.gateway.masking.time.time", lambda: next(clock))
    first = MaskSession.for_call([], stored_history=True)
    earlier = first.placeholder("PERSON", "h1", "Ayşe", from_input=True)
    second = MaskSession.for_call([], stored_history=True)
    later = second.placeholder("PERSON", "h2", "Mehmet", from_input=True)
    number = lambda token: int(token[len("<PERSON_") : -1])  # noqa: E731
    assert number(later) > number(earlier) > 1


# ------------------------------------------------------------------ event-stream lines


def _response(*chunks: bytes) -> httpx.Response:
    async def _body():
        for chunk in chunks:
            yield chunk

    return httpx.Response(200, content=_body())


async def test_unicode_line_separators_inside_data_do_not_split_the_line() -> None:
    payload = json.dumps({"text": "a b c\x85d\x0ce"}, ensure_ascii=False)
    stream = _response(f"data: {payload}\n\n".encode())
    assert [data async for data in sse_events(stream)] == [payload]


async def test_only_crlf_lf_and_cr_end_lines() -> None:
    stream = _response(b"data: 1\r", b"\n\r\ndata: 2\rdata: 3\n\n", b"data: 4")
    assert [line async for line in sse_lines(stream)] == [
        "data: 1",
        "",
        "data: 2",
        "data: 3",
        "",
        "data: 4",
    ]
