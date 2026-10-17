"""L0 behaviour on synthetic Turkish and English text."""

from __future__ import annotations

import pytest

from umai_decision_engine.contracts.bundle import L0Config
from umai_decision_engine.l0.detector import EntityDetector


def _found(detector: EntityDetector, text: str, config: L0Config, tenant: str = "t1"):
    result = detector.detect(text, tenant_id=tenant, config=config)
    return result, [(e.type, text[e.start : e.end]) for e in result.entities]


# ------------------------------------------------------------------ TCKN


@pytest.mark.parametrize(
    "text, value",
    [
        ("Müşterinin TC kimlik numarası 10000000146dır.", "10000000146"),  # Turkish suffix, no \b
        ("Customer national id 17291716060 attached", "17291716060"),
        ("KİMLİK NO: 100 000 001 46", "100 000 001 46"),  # grouped + uppercase Turkish context
        ("tckn:43770009392", "43770009392"),
    ],
)
def test_tckn_detected(detector, core_config, text, value) -> None:
    _, found = _found(detector, text, core_config)
    assert ("TR_TCKN", value) in found


def test_invalid_checksum_without_id_context_ignored(detector, core_config) -> None:
    _, found = _found(detector, "sipariş numarası 10000000147 kargoda", core_config)
    assert not [f for f in found if f[0] == "TR_TCKN"]


@pytest.mark.parametrize(
    "text",
    [
        "kimlik no 10000000147",
        "kimlik nom 10000000147",  # colloquial possessive
        "TC: 10000000147",
        "T.C. kimlik numaram 10000000147 idi",
    ],
)
def test_invalid_checksum_with_id_context_is_caught_unvalidated(
    detector, core_config, text
) -> None:
    # A typo'd or made-up ID next to "kimlik" is still an attempt to share an ID.
    result, found = _found(detector, text, core_config)
    assert found == [("TR_TCKN", "10000000147")]
    assert result.entities[0].validated is False


def test_validated_tckn_outranks_unvalidated(detector, core_config) -> None:
    result, _ = _found(detector, "kimlik 10000000146 ve kimlik 10000000147", core_config)
    assert [e.validated for e in result.entities] == [True, False]


def test_checksum_valid_tckn_is_caught_in_any_written_form(detector, core_config) -> None:
    # 1 % of random 11-digit strings pass the checksum: a valid one is reported grouped,
    # dashed or dotted, with or without ID context (ADR-0017 eval: separated forms were missed).
    for written in ("100 000 001 46", "100-000-001-46", "100.000.001.46"):
        _, found = _found(detector, f"kayıt {written} eksik", core_config)
        assert found == [("TR_TCKN", written)]


def test_grouped_invalid_checksum_needs_context(detector, core_config) -> None:
    _, found = _found(detector, "sipariş kodu 100 000 001 47", core_config)
    assert not [f for f in found if f[0] == "TR_TCKN"]


def test_placeholder_number_ignored(detector, core_config) -> None:
    _, found = _found(detector, "TCKN 11111111111", core_config)
    assert not found


# ------------------------------------------------------------------ IBAN


def test_tr_iban_with_spaces(detector, core_config) -> None:
    _, found = _found(detector, "IBAN: TR33 0006 1005 1978 6457 8413 26.", core_config)
    assert found == [("TR_IBAN", "TR33 0006 1005 1978 6457 8413 26")]


def test_tr_iban_glued_capitals_are_trimmed(detector, core_config) -> None:
    _, found = _found(detector, "hesap TR330006100519786457841326ABC", core_config)
    assert found == [("TR_IBAN", "TR330006100519786457841326")]


def test_foreign_iban_trailing_word(detector, core_config) -> None:
    _, found = _found(detector, "account DE89 3704 0044 0532 0130 00 ABC", core_config)
    assert found == [("IBAN_CODE", "DE89 3704 0044 0532 0130 00")]


def test_invalid_iban_ignored(detector, core_config) -> None:
    _, found = _found(detector, "IBAN TR34 0006 1005 1978 6457 8413 26", core_config)
    assert not found


# ------------------------------------------------------------------ phone


@pytest.mark.parametrize(
    "text, value",
    [
        ("tel: +90 532 123 45 67", "+90 532 123 45 67"),
        ("ara 0 (532) 123 45 67", "0 (532) 123 45 67"),
        ("0532-123-45-67 numaramdır", "0532-123-45-67"),
        ("cep 532 123 45 67", "532 123 45 67"),
        ("5321234567", "5321234567"),  # bare mobile alone: valid 5xx range, no context needed
        ("90 532 123 45 67", "90 532 123 45 67"),  # country code without '+'
        ("0090 532 123 45 67", "0090 532 123 45 67"),
        ("ofis 0212 345 67 89", "0212 345 67 89"),
    ],
)
def test_phone_detected(detector, core_config, text, value) -> None:
    _, found = _found(detector, text, core_config)
    assert ("TR_PHONE", value) in found


def test_bare_fixed_line_without_context_ignored(detector, core_config) -> None:
    # Bare 10-digit landline numbers collide with order/reference numbers: context required.
    _, found = _found(detector, "referans 212 345 67 89", core_config)
    assert not found
    _, found = _found(detector, "ofis telefonu 212 345 67 89", core_config)
    assert found == [("TR_PHONE", "212 345 67 89")]


def test_bare_ten_digits_outside_mobile_ranges_ignored(detector, core_config) -> None:
    _, found = _found(detector, "5991234567", core_config)  # 599 is not an assigned mobile range
    assert not found


def test_invalid_phone_range_ignored(detector, core_config) -> None:
    _, found = _found(detector, "telefon 0600 123 45 67", core_config)
    assert not found


# ------------------------------------------------------------------ cards and e-mail


@pytest.mark.parametrize(
    "value", ["4242 4242 4242 4242", "9792 0000 0000 0003", "2223003122003222", "3782 822463 10005"]
)
def test_cards_detected(detector, core_config, value) -> None:
    _, found = _found(detector, f"kart numarası {value}", core_config)
    assert ("CREDIT_CARD", value) in found


def test_card_luhn_failure_ignored(detector, core_config) -> None:
    _, found = _found(detector, "kart 4242424242424241", core_config)
    assert not found


def test_email(detector, core_config) -> None:
    _, found = _found(detector, "mail: ali.veli+test@example.com.tr.", core_config)
    assert found == [("EMAIL_ADDRESS", "ali.veli+test@example.com.tr")]


# ------------------------------------------------------------------ normalization and evasion


def test_full_width_and_zero_width(detector, core_config) -> None:
    text = "TC １０００００００１４６ ve 1000​0000146"
    result, found = _found(detector, text, core_config)
    assert [f[0] for f in found] == ["TR_TCKN", "TR_TCKN"]
    # Same canonical value → same placeholder and one distinct value.
    assert result.entities[0].placeholder == result.entities[1].placeholder == "<TR_TCKN_1>"
    assert result.summary[0].count == 2 and result.summary[0].distinct == 1


# ------------------------------------------------------------------ config


def test_entity_subset_and_threshold(detector) -> None:
    config = L0Config(entities=["EMAIL_ADDRESS"])
    _, found = _found(detector, "TC 10000000146 mail a@b.co", config)
    assert found == [("EMAIL_ADDRESS", "a@b.co")]
    strict = L0Config(entities=["TR_TCKN"], min_score_overrides={"TR_TCKN": 0.95})
    _, found = _found(detector, "numara 10000000146", strict)
    assert not found


def test_long_input_is_chunked(detector) -> None:
    config = L0Config(entities=["TR_TCKN"], max_chars=1000)
    filler = "lorem ipsum " * 200
    text = f"{filler} TC 10000000146 {filler} TC 17291716060"
    result, found = _found(detector, text, config)
    assert [v for _, v in found] == ["10000000146", "17291716060"]
    assert result.truncated


# ------------------------------------------------------------------ redaction, hashing, leaks


def test_placeholders_numbered_per_type(detector, core_config) -> None:
    text = "TC 10000000146, TC 17291716060, tekrar 10000000146, mail a@b.co"
    result = detector.detect(text, tenant_id="t1", config=core_config)
    assert result.redacted_text == (
        "TC <TR_TCKN_1>, TC <TR_TCKN_2>, tekrar <TR_TCKN_1>, mail <EMAIL_ADDRESS_1>"
    )


def test_hash_is_per_tenant_and_deterministic(detector, core_config) -> None:
    a1 = detector.detect("TC 10000000146", tenant_id="a", config=core_config).entities[0]
    a2 = detector.detect("tc kimlik 10000000146", tenant_id="a", config=core_config).entities[0]
    b = detector.detect("TC 10000000146", tenant_id="b", config=core_config).entities[0]
    assert a1.value_hash == a2.value_hash
    assert a1.value_hash != b.value_hash


def test_no_raw_value_leaks(detector, core_config) -> None:
    text = (
        "TC 10000000146, IBAN TR33 0006 1005 1978 6457 8413 26, tel +90 532 123 45 67, "
        "kart 4242 4242 4242 4242, mail ali@example.com"
    )
    result = detector.detect(text, tenant_id="t1", config=core_config)
    assert len(result.entities) == 5
    dump = result.model_dump_json()
    raw_values = [text[e.start : e.end] for e in result.entities]
    for raw in raw_values:
        for form in {raw, raw.replace(" ", ""), raw.replace(" ", "").replace("+", "")}:
            assert form not in dump
    assert all(raw not in result.redacted_text for raw in raw_values)
