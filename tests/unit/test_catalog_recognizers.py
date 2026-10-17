"""Catalog recognizers (ADR-0017): Lakera parity, Türkiye, Europe, secrets. Synthetic data only.

Provider tokens are assembled from pieces at runtime so that no file in the repository
contains a string a secret scanner would treat as a real credential.
"""

from __future__ import annotations

import pytest

from umai_decision_engine.contracts.bundle import CustomPattern, CustomRecognizer, L0Config
from umai_decision_engine.l0.normalize import normalize
from umai_decision_engine.l0.recognizers.catalog import SPECS_BY_ENTITY, TAG_BY_ENTITY
from umai_decision_engine.l0.recognizers.custom import pattern_problems
from umai_decision_engine.l0.recognizers.network import registrable_domain, url_domain
from umai_decision_engine.l0.validators import (
    de_tax_id_is_valid,
    es_nie_is_valid,
    es_nif_is_valid,
    fr_nir_is_valid,
    it_fiscal_code_is_valid,
    nl_bsn_is_valid,
    pl_pesel_is_valid,
    uk_nino_is_valid,
    us_ssn_is_valid,
)

ALL = L0Config(entities=sorted(SPECS_BY_ENTITY))


def _found(detector, text: str, config: L0Config = ALL) -> list[tuple[str, str]]:
    result = detector.detect(text, tenant_id="t", config=config)
    return [(e.type, text[e.start : e.end]) for e in result.entities]


def test_every_entity_has_a_tag() -> None:
    assert set(SPECS_BY_ENTITY) == set(TAG_BY_ENTITY)


# ------------------------------------------------------------------ validators


@pytest.mark.parametrize(
    ("check", "valid", "invalid"),
    [
        (
            us_ssn_is_valid,
            ["234567890", "078234561"],
            ["000123456", "666451234", "900123456", "234007890", "234560000"],
        ),
        (
            uk_nino_is_valid,
            ["JG103759A", "AB123456C"],
            ["QQ123456C", "GB123456A", "JG103759E", "DA123456A"],
        ),
        (de_tax_id_is_valid, ["86095742719"], ["86095742718", "12345678901", "01234567891"]),
        (fr_nir_is_valid, ["185077511500322"], ["185077511500323", "385077511500322"]),
        (es_nif_is_valid, ["12345678Z", "12345678z"], ["12345678A", "1234567Z"]),
        (es_nie_is_valid, ["X1234567L"], ["X1234567A", "A1234567L"]),
        (it_fiscal_code_is_valid, ["RSSMRA85T10A562S"], ["RSSMRA85T10A562T", "RSSMRA85T10A56"]),
        (nl_bsn_is_valid, ["111222333", "123456782"], ["111222334", "000000000"]),
        (pl_pesel_is_valid, ["44051401359"], ["44051401358", "44133101352"]),
    ],
)
def test_validators(check, valid, invalid) -> None:
    assert all(check(v) for v in valid)
    assert not any(check(v) for v in invalid)


def test_fr_nir_vector_is_computed() -> None:
    body = "1850775115003"
    assert fr_nir_is_valid(body + f"{97 - int(body) % 97:02d}")


# ------------------------------------------------------------------ detection


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Lakera parity
        ("Login from 85.105.12.34 was blocked.", ("IP_ADDRESS", "85.105.12.34")),
        ("Ofis yazıcısı 192.168.1.10 adresinde.", ("IP_ADDRESS", "192.168.1.10")),
        ("IPv6 adresi: 2a02:ff0:3301:12::1", ("IP_ADDRESS", "2a02:ff0:3301:12::1")),
        ("SSN: 234-56-7890", ("US_SSN", "234-56-7890")),
        ("social security no 456789012", ("US_SSN", "456789012")),
        ("Call me at (415) 555-0132.", ("PHONE_NUMBER", "(415) 555-0132")),
        ("Call our London office on +44 20 7946 0958.", ("PHONE_NUMBER", "+44 20 7946 0958")),
        ("Appelez-moi au 06 39 98 12 34.", ("PHONE_NUMBER", "06 39 98 12 34")),
        ("Mail: ali.veli [at] example.com", ("EMAIL_ADDRESS", "ali.veli [at] example.com")),
        (
            "e-postam ali.veli at example nokta com",
            ("EMAIL_ADDRESS", "ali.veli at example nokta com"),
        ),
        ("Diners kartım 3056 930902 5904", ("CREDIT_CARD", "3056 930902 5904")),
        (
            "Click https://secure-hesap-dogrula.xyz/login now.",
            ("UNKNOWN_LINK", "https://secure-hesap-dogrula.xyz/login"),
        ),
        (
            "Ship it to 742 Evergreen Terrace, Springfield, OR 97477.",
            ("ADDRESS", "742 Evergreen Terrace, Springfield, OR 97477"),
        ),
        ("Müşteri adı Ayşe Demir, randevusunu yarına al.", ("PERSON", "Ayşe Demir")),
        ("Please schedule a call with James O'Brien.", ("PERSON", "James O'Brien")),
        # Türkiye
        ("Şirketin vergi kimlik numarası 4540536920.", ("TR_VKN", "4540536920")),
        ("Vergi no 5555555553 olan tedarikçi", ("TR_VKN", "5555555553")),  # not a phone here
        ("Aracın plakası 34 ABC 123.", ("TR_PLATE", "34 ABC 123")),
        (
            "Teslimat adresi: Caferağa Mah. Moda Cad. No: 12 D: 4 Kadıköy/İstanbul",
            ("ADDRESS", "Caferağa Mah. Moda Cad. No: 12 D: 4 Kadıköy/İstanbul"),
        ),
        ("Dosyayı Elif Koç'a gönder.", ("PERSON", "Elif Koç")),
        # Europe
        ("National Insurance number: JG 10 37 59 A", ("UK_NINO", "JG 10 37 59 A")),
        ("Steuer-ID: 86095742719", ("DE_TAX_ID", "86095742719")),
        ("NIR 1 85 07 75 115 003 22", ("FR_NIR", "1 85 07 75 115 003 22")),
        ("Mi DNI es 12345678Z.", ("ES_NIF", "12345678Z")),
        ("NIE X1234567L", ("ES_NIE", "X1234567L")),
        ("Codice fiscale: RSSMRA85T10A562S", ("IT_FISCAL_CODE", "RSSMRA85T10A562S")),
        ("BSN: 111222333", ("NL_BSN", "111222333")),
        ("PESEL: 44051401359", ("PL_PESEL", "44051401359")),
        ("Lieferadresse: Hauptstraße 5, 10115 Berlin", ("ADDRESS", "Hauptstraße 5, 10115 Berlin")),
        (
            "Send the contract to 12 Rue de la Paix, 75002 Paris.",
            ("ADDRESS", "12 Rue de la Paix, 75002 Paris"),
        ),
        # secrets (documentation examples only)
        ("aws_access_key_id = AKIAIOSFODNN7EXAMPLE", ("API_KEY", "AKIAIOSFODNN7EXAMPLE")),
        (
            "DATABASE_URL=postgres://app:S3cretPass@db.example.com:5432/orders",
            ("CONNECTION_STRING", "postgres://app:S3cretPass@db.example.com:5432/orders"),
        ),
        (
            "mysql://reporter:R3port!ng@10.0.0.5/analytics",
            ("CONNECTION_STRING", "mysql://reporter:R3port!ng@10.0.0.5/analytics"),
        ),
        ("DB_PASSWORD=pr0d-Secret-77", ("PASSWORD", "pr0d-Secret-77")),
        ("Wi-Fi şifresi Misafir2026! olarak değişti", ("PASSWORD", "Misafir2026!")),
    ],
)
def test_catalog_positives(detector, text, expected) -> None:
    assert expected in _found(detector, text)


@pytest.mark.parametrize(
    "text",
    [
        "Yerel test için 127.0.0.1 kullan.",
        "DNS sunucusu olarak 8.8.8.8 ayarlı.",
        "Alt ağ maskesi 255.255.255.0 olmalı.",
        "Sürüm 10.4.2 yayınlandı; v1.2.3.4 etiketi de var.",
        "SSN 000-12-3456 is not a valid number.",
        "Order 900-12-3456 shipped.",
        "Sipariş numaranız 50498050774, kargoya verildi.",
        "Lot numarası 5991234567",
        "ISBN 978-605-09-1234-5 numaralı kitabı sipariş et.",
        "Kaynak: https://tr.wikipedia.org/wiki/Istanbul",
        "Repo: https://github.com/UMAI-Solutions",
        "Toplantı İstanbul'da yapılacak.",
        "Ahmet toplantıya geldi mi?",
        "Mehmet Bey aradı, dönüş bekliyor.",
        "John Doe örnek kullanıcı adıdır.",
        "Toplantı 12 eki 2024'te yapıldı.",
        "Şifre en az 8 karakter olmalı.",
        "-----BEGIN PUBLIC KEY-----",
        "postgres://db.example.com:5432/app adresine bağlan.",
        "Referans 123456789 ile kayıtlı.",
        "Firmanın vergi numarası 1234567890.",  # checksum-valid but a known non-PII example
    ],
)
def test_catalog_negatives(detector, text) -> None:
    assert _found(detector, text) == []


def test_provider_tokens(detector) -> None:
    body = "Ab1Cd2Ef3Gh4" * 4  # 48 synthetic characters, assembled at runtime
    tokens = {
        "gh" + "p_" + body[:36]: "API_KEY",
        "github" + "_pat_" + body[:30]: "API_KEY",
        "xo" + "xb-" + "1234567890-" + body[:24]: "API_KEY",
        "sk" + "_test_" + body[:24]: "API_KEY",
        "sk-" + "ant-" + body[:32]: "API_KEY",
        "sk-" + "proj-" + body[:32]: "API_KEY",
        "AI" + "za" + body[:35]: "API_KEY",
    }
    for token, entity in tokens.items():
        assert (entity, token) in _found(detector, f"Bu anahtarı kullan: {token}"), token


def test_private_key_block_and_jwt(detector) -> None:
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAexample\n-----END RSA PRIVATE KEY-----"
    assert ("PRIVATE_KEY", key) in _found(detector, f"Anahtar:\n{key}\nbitti")
    header, payload = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    jwt = f"{header}.{payload}.c2lnbmF0dXJlLWV4YW1wbGU"
    assert ("JWT", jwt) in _found(detector, f"Authorization: Bearer {jwt}")
    not_jwt = f"eyJub3RfanNvbiI.{payload}.c2lnbmF0dXJl"
    assert not [t for t, _ in _found(detector, not_jwt) if t == "JWT"]


def test_digit_evasion_is_undone(detector) -> None:
    assert normalize("1 0 0 0 0 0 0 0 1 4 6").text == "10000000146"
    assert normalize("1OOOOOOO146").text == "10000000146"
    assert normalize("tel 0532 123 45 67").text == "tel 0532 123 45 67"
    text = "Kimlik numaram: 1 0 0 0 0 0 0 0 1 4 6"
    assert _found(detector, text) == [("TR_TCKN", "1 0 0 0 0 0 0 0 1 4 6")]


def test_turkish_phone_not_read_inside_foreign_number(detector) -> None:
    assert _found(detector, "Call our London office on +44 20 7946 0958.") == [
        ("PHONE_NUMBER", "+44 20 7946 0958")
    ]


# ------------------------------------------------------------------ guardrail data


def test_allowed_domains_suppress_unknown_links(detector) -> None:
    text = "Belge: https://docs.partner-portal.xyz/f/1"
    assert _found(detector, text) == [("UNKNOWN_LINK", "https://docs.partner-portal.xyz/f/1")]
    trusted = L0Config(entities=["UNKNOWN_LINK"], allowed_domains=["partner-portal.xyz"])
    assert _found(detector, text, trusted) == []


def test_domain_helpers() -> None:
    assert registrable_domain("www.hepsiburada.com.tr") == "hepsiburada.com.tr"
    assert registrable_domain("tr.wikipedia.org") == "wikipedia.org"
    assert url_domain("www.example-unknown.xyz/a?b=c") == "example-unknown.xyz"
    assert url_domain("http://10.0.0.5/admin") is None


def test_custom_recognizer(detector) -> None:
    config = L0Config(
        entities=["TR_TCKN", "CUSTOMER_NO"],
        custom_recognizers=[
            CustomRecognizer(
                entity="CUSTOMER_NO",
                patterns=[
                    CustomPattern(
                        name="cust", regex=r"(?<![A-Z0-9])MUS-[0-9]{6}(?![0-9])", score=0.3
                    )
                ],
                context=["müşteri no", "customer"],
            ),
            CustomRecognizer(
                entity="LOYALTY_CARD",
                patterns=[
                    CustomPattern(name="card", regex=r"(?<![0-9])[0-9]{16}(?![0-9])", score=0.6)
                ],
                validator="luhn",
            ),
        ],
    )
    config.entities.append("LOYALTY_CARD")
    assert _found(detector, "Müşteri no MUS-123456 iptal", config) == [
        ("CUSTOMER_NO", "MUS-123456")
    ]
    assert _found(detector, "Kod MUS-123456 iptal", config) == []  # 0.3 without context
    assert _found(detector, "Kart 4242424242424242", config) == [
        ("LOYALTY_CARD", "4242424242424242")
    ]
    assert _found(detector, "Kart 4242424242424241", config) == []


@pytest.mark.parametrize(
    ("pattern", "problem"),
    [
        ("(a+)+", "nested unbounded quantifier"),
        (r"(\w+\s?)*x", "nested unbounded quantifier"),
        ("[a-z", "does not compile"),
        ("a*", "matches the empty string"),
    ],
)
def test_custom_pattern_safety(pattern, problem) -> None:
    assert any(problem in p for p in pattern_problems(pattern))
    assert pattern_problems(r"MUS-[0-9]{6}") == []
