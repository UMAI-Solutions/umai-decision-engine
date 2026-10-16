"""Vectors from docs/reference/turkish-identifiers.md (computed and re-checked 2026-10-15)."""

import pytest

from umai_decision_engine.l0.validators import (
    card_is_valid,
    iban_mod97_is_valid,
    luhn_is_valid,
    tckn_is_valid,
    tr_iban_is_valid,
    vkn_is_valid,
)


@pytest.mark.parametrize(
    "value",
    [
        "17291716060",
        "10000000146",
        "12345678950",
        "43770009392",
        "96696370398",
        "92041534996",
        "72000876268",
        "99123456740",
    ],
)
def test_tckn_valid(value: str) -> None:
    assert tckn_is_valid(value)


@pytest.mark.parametrize(
    "value",
    [
        "17291716050",
        "07291716092",
        "1729171606",
        "12345678901",
        "10000000147",
        "43770009393",
        "96696370399",
        "11111111111",
        "",
        "1729171606a",
        "１７２９１７１６０６０",
    ],
)
def test_tckn_invalid(value: str) -> None:
    assert not tckn_is_valid(value)


@pytest.mark.parametrize(
    "value", ["4540536920", "9876543217", "5555555553", "1000000000", "0000000019", "1234567890"]
)
def test_vkn_valid(value: str) -> None:
    assert vkn_is_valid(value)


@pytest.mark.parametrize("value", ["4540536921", "0000000000", "1111111111", "454053692"])
def test_vkn_invalid(value: str) -> None:
    assert not vkn_is_valid(value)


@pytest.mark.parametrize(
    "value",
    ["TR330006100519786457841326", "TR750009900000001234567890", "TR57123450ABCD000000000001"],
)
def test_tr_iban_valid(value: str) -> None:
    assert tr_iban_is_valid(value)
    assert iban_mod97_is_valid(value)


@pytest.mark.parametrize(
    "value",
    [
        "TR340006100519786457841326",
        "TR750090900000001234567890",
        "TR33000610051978645784132",
        "TR330006110519786457841326",
    ],  # last: reserved digit is not 0
)
def test_tr_iban_invalid(value: str) -> None:
    assert not tr_iban_is_valid(value)


def test_generic_iban() -> None:
    assert iban_mod97_is_valid("DE89370400440532013000")
    assert not iban_mod97_is_valid("DE89370400440532013001")


@pytest.mark.parametrize(
    "value",
    [
        "4242424242424242",
        "5555555555554444",
        "2223003122003222",
        "378282246310005",
        "9792000000000003",
    ],
)
def test_card_valid(value: str) -> None:
    assert card_is_valid(value)


def test_card_invalid() -> None:
    assert not card_is_valid("4242424242424241")  # Luhn
    assert luhn_is_valid("0000000000000000")
    assert not card_is_valid("0000000000000000")  # Luhn-valid but unknown issuer
    assert luhn_is_valid("17893729974")
    assert not luhn_is_valid("17893729975")
