"""Generate the synthetic `l0-pii-tr` dataset (docs/evals/README.md).

    python -m evals.datasets.generate_l0_pii_tr          # writes l0-pii-tr/v2.jsonl

Deterministic: the same code always writes the same file (fixed seed, no clock). Every value
is synthetic (AGENTS.md rule 2):

- TCKN / VKN: vectors from docs/reference/turkish-identifiers.md plus values generated with a
  valid checksum in fixed families (TCKN `10000xxxxxx`); no names or other attributes attached.
- IBAN: documented examples plus bank code 00099 with generated account numbers (valid mod 97).
- Phone: valid operator prefixes with a small set of placeholder subscriber numbers.
- Cards: published test numbers (Stripe) and synthetic TROY numbers (IIN 9792, valid Luhn).
- E-mail: reserved example domains only (RFC 2606). Names: common first + last names only.
- v2 (ADR-0017, Lakera parity + Türkiye + Europe): US SSNs in patterned valid ranges, US phones
  in the fictional 555-01xx range, UK / FR phones in the regulators' drama/fiction ranges,
  canonical example national IDs (ES DNI 12345678Z, IT RSSMRA85T10A562S, PL 44051401359, NL
  111222333, DE 86095742719) and values generated with the same checksums, fictional or generic
  street addresses, made-up phishing-style domains for unknown links.

Labels state the truth, not what the detector can do today: entity types L0 does not support
yet are labelled so that the eval shows the gap (docs/architecture/l0-entity-catalog.md).
v1.jsonl is kept as the frozen first version; this module writes v2.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

DATASET = Path(__file__).resolve().parent / "l0-pii-tr" / "v2.jsonl"
SEED = 20261017
TEST_SHARE = 3  # of 10: ~30 % test split, chosen by case-id hash

_rng = random.Random(SEED)

# ------------------------------------------------------------------ synthetic values


def _tckn_from9(first9: str) -> str:
    d = [int(c) for c in first9]
    d10 = (7 * (d[0] + d[2] + d[4] + d[6] + d[8]) - (d[1] + d[3] + d[5] + d[7])) % 10
    d11 = (sum(d) + d10) % 10
    return f"{first9}{d10}{d11}"


def _tckn_valid(value: str) -> bool:
    return len(value) == 11 and value[0] != "0" and _tckn_from9(value[:9]) == value


TCKN_DOC = [
    "10000000146",
    "17291716060",
    "12345678950",
    "43770009392",
    "96696370398",
    "92041534996",
    "72000876268",
]
TCKN = TCKN_DOC + [_tckn_from9(f"10000{_rng.randrange(10_000):04d}") for _ in range(13)]


def _tckn_invalid() -> str:
    while True:
        value = str(_rng.randrange(10**10, 10**11))
        if not _tckn_valid(value) and len(set(value)) > 3:
            return value


def _vkn_check(first9: str) -> str:
    total = 0
    for i, ch in enumerate(first9, start=1):
        p = (int(ch) + 10 - i) % 10
        if p:
            q = (p * 2 ** (10 - i)) % 9
            total += q or 9
    return first9 + str((10 - total % 10) % 10)


VKN = ["4540536920", "9876543217", "5555555553"] + [
    _vkn_check(f"{_rng.randrange(1, 10)}{_rng.randrange(10**8):08d}") for _ in range(5)
]


def _iban_tr(bban: str) -> str:
    digits = "".join(str(int(c, 36)) for c in bban + "TR00")
    return f"TR{98 - int(digits) % 97:02d}{bban}"


TR_IBAN = ["TR330006100519786457841326", "TR750009900000001234567890"] + [
    _iban_tr(f"000990{_rng.randrange(10**16):016d}") for _ in range(8)
]
FOREIGN_IBAN = ["DE89370400440532013000", "GB82WEST12345698765432"]

_MOBILE_PREFIXES = [
    "501",
    "505",
    "506",
    "507",
    "530",
    "532",
    "533",
    "535",
    "537",
    "538",
    "541",
    "542",
    "543",
    "544",
    "545",
    "549",
    "551",
    "552",
    "553",
    "555",
]
_SUBSCRIBERS = [
    "1234567",
    "0000001",
    "1112233",
    "9876543",
    "0001122",
    "4445566",
    "1231231",
    "7654321",
]
MOBILE = [p + _SUBSCRIBERS[i % len(_SUBSCRIBERS)] for i, p in enumerate(_MOBILE_PREFIXES)]
FIXED = ["2123456789", "3120001122", "2161234567", "2329876543", "8501234567"]


def _luhn_complete(prefix: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(prefix)):
        n = int(ch)
        if i % 2 == 0:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return prefix + str((10 - total % 10) % 10)


CARDS = [
    "4242424242424242",
    "5555555555554444",
    "2223003122003222",
    "9792000000000003",
    "4000056655665556",
] + [_luhn_complete(f"979200000000{i:03d}") for i in range(1, 6)]
AMEX = ["378282246310005"]

EMAILS = [
    "ali.veli@example.com.tr",
    "ayse.yilmaz@example.com",
    "mehmet_kaya@example.org",
    "destek@example.com.tr",
    "test.user+fatura@example.com",
    "can.ozturk@mail.example.net",
    "Zeynep.Arslan@Example.com",
    "info@example.org",
]
NAMES = [
    "Ahmet Yılmaz",
    "Ayşe Demir",
    "Mehmet Kaya",
    "Fatma Şahin",
    "Mustafa Çelik",
    "Zeynep Arslan",
    "Emre Doğan",
    "Elif Koç",
    "John Smith",
    "Maria Garcia",
]


def _cycle(values: list[str]) -> Callable[[], str]:
    state = {"i": 0}

    def _next() -> str:
        value = values[state["i"] % len(values)]
        state["i"] += 1
        return value

    return _next


# ------------------------------------------------------------------ written forms


def tckn_forms(v: str) -> dict[str, str]:
    return {
        "plain": v,
        "grouped": f"{v[:3]} {v[3:6]} {v[6:9]} {v[9:]}",
        "dashed": f"{v[:3]}-{v[3:6]}-{v[6:9]}-{v[9:]}",
        "dotted": f"{v[:3]}.{v[3:6]}.{v[6:9]}.{v[9:]}",
    }


def phone_forms(nsn: str) -> dict[str, str]:
    a, b, c, d = nsn[:3], nsn[3:6], nsn[6:8], nsn[8:]
    return {
        "intl": f"+90 {a} {b} {c} {d}",
        "intl_compact": f"+90{nsn}",
        "trunk": f"0{a} {b} {c} {d}",
        "trunk_compact": f"0{nsn}",
        "paren": f"0 ({a}) {b} {c} {d}",
        "dashed": f"0{a}-{b}-{c}-{d}",
        "bare_grouped": f"{a} {b} {c} {d}",
        "bare": nsn,
        "double_zero": f"0090 {a} {b} {c} {d}",
        "cc_no_plus": f"90 {a} {b}{c}{d}",
    }


def iban_forms(v: str) -> dict[str, str]:
    groups = " ".join(v[i : i + 4] for i in range(0, len(v), 4))
    return {"grouped": groups, "compact": v}


def card_forms(v: str) -> dict[str, str]:
    groups = [v[i : i + 4] for i in range(0, len(v), 4)]
    return {"grouped": " ".join(groups), "dashed": "-".join(groups), "compact": v}


# ------------------------------------------------------------------ cases


@dataclass
class Case:
    text: str
    expected: list[dict[str, object]]
    tags: list[str]
    lang: str = "tr"
    extra: dict[str, object] = field(default_factory=dict)


_SLOT = re.compile(r"\{(\w+)\}")


def render(template: str, tags: list[str], lang: str = "tr", **slots: tuple[str, str]) -> Case:
    """``slots``: name → (entity type, written value); an empty type means "not an entity"."""

    text, expected, cursor = "", [], 0
    for match in _SLOT.finditer(template):
        text += template[cursor : match.start()]
        entity_type, value = slots[match.group(1)]
        if entity_type:
            expected.append(
                {"type": entity_type, "start": len(text), "end": len(text) + len(value)}
            )
        text += value
        cursor = match.end()
    text += template[cursor:]
    return Case(text=text, expected=expected, tags=tags, lang=lang)


def _tckn_cases() -> Iterator[Case]:
    value = _cycle(TCKN)
    forms = _cycle(["plain", "grouped", "plain", "dashed", "plain", "dotted"])
    formal = [
        "Müşterinin TC kimlik numarası {v}, kaydını kontrol eder misin?",
        "T.C. Kimlik No: {v}",
        "TCKN {v} olan başvurunun durumu nedir?",
        "Kimlik numarası {v} olan vatandaşın poliçesini yenile.",
        "Başvuru formunda TC kimlik no alanına {v} yazılmış.",
        "Lütfen {v} T.C. kimlik numaralı müşterinin adresini güncelle.",
        "Vatandaşlık numarası: {v}",
    ]
    colloquial = [
        "kimlik nom {v}",
        "tc no {v} bakar mısın",
        "tcm {v} bu arada",
        "TC'm {v}, sisteme girer misin?",
        "kimlik numaram {v} hocam",
        "tc kimlik {v} acil lazım",
    ]
    english = [
        "My Turkish national ID is {v}, please verify.",
        "National ID number: {v}",
        "Customer TCKN {v} needs a KYC refresh.",
        "Can you check ID no {v} in the CRM?",
    ]
    no_context = ["{v}", "{v} numaralı kayıt eksik.", "Kayıt: {v}"]
    for group, templates, lang in (
        ("context_formal", formal, "tr"),
        ("context_colloquial", colloquial, "tr"),
        ("context_en", english, "en"),
        ("no_context", no_context, "tr"),
    ):
        for template in templates * 2:
            fmt = forms()
            yield render(
                template,
                ["tckn", group, f"format_{fmt}"],
                lang,
                v=("TR_TCKN", tckn_forms(value())[fmt]),
            )
    for template in [
        "Kimlik numarası {v}'dir.",
        "TC no {v}'yi sisteme gir.",
        "TCKN'si {v}'tir.",
        "{v}'nin kaydı güncellendi, kimlik no doğru mu?",
    ]:
        yield render(template, ["tckn", "suffix"], v=("TR_TCKN", value()))
    for template in ["KİMLİK NO: {v}", "T.C. KİMLİK NUMARASI {v}", "TC KİMLİK {v} OLAN MÜŞTERİ"]:
        yield render(template, ["tckn", "uppercase"], v=("TR_TCKN", value()))
    # a mistyped or made-up ID next to an ID word is still an attempt to share one
    for template in ["kimlik nom {v}", "TC kimlik no {v}", "T.C. kimlik numaram {v} idi"]:
        yield render(
            template, ["tckn", "invalid_checksum_with_context"], v=("TR_TCKN", _tckn_invalid())
        )
    v = value()
    adversarial = {
        "single_digit_spaced": " ".join(v),
        "zero_width": v[:4] + "​" + v[4:8] + "​" + v[8:],
        "fullwidth": "".join(chr(0xFF10 + int(c)) for c in v),
        "mixed_separators": f"{v[:3]}.{v[3:6]}-{v[6:9]} {v[9:]}",
        "newline_split": f"{v[:6]}\n{v[6:]}",
        "letter_o_for_zero": v.replace("0", "O"),
    }
    for name, written in adversarial.items():
        yield render(
            "Kimlik numaram: {v}", ["tckn", "adversarial", f"adv_{name}"], v=("TR_TCKN", written)
        )
    yield render(
        "Kimlik numaram {v}.",
        ["tckn", "adversarial", "adv_words"],
        v=("TR_TCKN", "bir sıfır sıfır sıfır sıfır sıfır sıfır sıfır bir dört altı"),
    )
    yield render(
        "Hello, my TC kimlik no is {v}, bunu sisteme girer misin please?",
        ["tckn", "code_switch"],
        "mixed",
        v=("TR_TCKN", value()),
    )


def _phone_cases() -> Iterator[Case]:
    mobile = _cycle(MOBILE)
    forms = _cycle(list(phone_forms("5321234567")))
    contexts = [
        ("Müşteriyi {v} numarasından aramamı hatırlat.", "context_formal", "tr"),
        ("Cep telefonu: {v}", "context_formal", "tr"),
        ("İletişim numarası {v} olarak güncellendi.", "context_formal", "tr"),
        ("telim {v} ulaşamazsan buradan yaz", "context_colloquial", "tr"),
        ("numaram {v} whatsapptan yaz", "context_colloquial", "tr"),
        ("cepim {v}", "context_colloquial", "tr"),
        ("Call the customer at {v} tomorrow.", "context_en", "en"),
        ("Phone: {v}", "context_en", "en"),
        ("{v}", "no_context", "tr"),
        ("{v} bu numaraya dönüş yapılacak", "no_context", "tr"),
    ]
    for template, group, lang in contexts:
        for _ in range(3):
            fmt = forms()
            yield render(
                template,
                ["phone", group, f"format_{fmt}"],
                lang,
                v=("TR_PHONE", phone_forms(mobile())[fmt]),
            )
    for template in ["Numarası {v}'dir.", "{v}'yi aradım, açmadı.", "Telefonu {v}'den arayın."]:
        yield render(template, ["phone", "suffix"], v=("TR_PHONE", phone_forms(mobile())["trunk"]))
    fixed = _cycle(FIXED)
    for template in [
        "Ofis telefonu {v}",
        "Sabit hat: {v}",
        "Bizi {v} numaralı telefondan arayın.",
        "İş yeri tel: {v}",
    ]:
        nsn = fixed()
        yield render(template, ["phone", "fixed_line"], v=("TR_PHONE", phone_forms(nsn)["trunk"]))
    nsn = mobile()
    for name, written in {
        "single_digit_spaced": " ".join("0" + nsn),
        "zero_width": "0" + nsn[:3] + "​" + nsn[3:],
        "fullwidth": "".join(chr(0xFF10 + int(c)) for c in "0" + nsn),
        "dots": f"0{nsn[:3]}.{nsn[3:6]}.{nsn[6:8]}.{nsn[8:]}",
    }.items():
        yield render(
            "Telefonum {v}", ["phone", "adversarial", f"adv_{name}"], v=("TR_PHONE", written)
        )


def _iban_cases() -> Iterator[Case]:
    value = _cycle(TR_IBAN)
    contexts = [
        ("Ödemeyi {v} hesabına yaptım, dekontu yaz.", "context_formal", "tr"),
        ("IBAN: {v}", "context_formal", "tr"),
        ("Maaş {v} IBAN numaralı hesaba yatacak.", "context_formal", "tr"),
        ("ibanım {v} oraya at", "context_colloquial", "tr"),
        ("Please send the refund to {v}.", "context_en", "en"),
        ("{v}", "no_context", "tr"),
    ]
    for template, group, lang in contexts:
        for fmt in ("grouped", "compact"):
            yield render(
                template,
                ["iban", group, f"format_{fmt}"],
                lang,
                v=("TR_IBAN", iban_forms(value())[fmt]),
            )
    yield render("IBAN {v}'ye gönderildi.", ["iban", "suffix"], v=("TR_IBAN", value()))
    yield render(
        "iban: {v}",
        ["iban", "adversarial", "adv_lowercase"],
        v=("TR_IBAN", iban_forms(value())["grouped"].lower()),
    )
    v = value()
    yield render(
        "IBAN: {v}",
        ["iban", "adversarial", "adv_dashed"],
        v=("TR_IBAN", "-".join(v[i : i + 4] for i in range(0, len(v), 4))),
    )
    for template, v in (
        ("German supplier IBAN {v}", FOREIGN_IBAN[0]),
        ("UK hesabı: {v}", FOREIGN_IBAN[1]),
    ):
        yield render(template, ["iban", "foreign"], "mixed", v=("IBAN_CODE", v))


def _card_cases() -> Iterator[Case]:
    value = _cycle(CARDS)
    contexts = [
        ("Kart numaram {v}, son ödeme tarihini söyle.", "context_formal", "tr"),
        ("Kredi kartı: {v}", "context_formal", "tr"),
        ("Müşteri {v} numaralı kartla ödeme yaptı.", "context_formal", "tr"),
        ("kartım {v} bunu kaydet", "context_colloquial", "tr"),
        ("Card number {v}, expiry 12/28.", "context_en", "en"),
        ("{v}", "no_context", "tr"),
    ]
    for template, group, lang in contexts:
        for fmt in ("grouped", "dashed", "compact"):
            yield render(
                template,
                ["card", group, f"format_{fmt}"],
                lang,
                v=("CREDIT_CARD", card_forms(value())[fmt]),
            )
    yield render("Amex kartım {v}", ["card", "amex"], v=("CREDIT_CARD", "3782 822463 10005"))
    yield render("Kart {v}'yle ödendi.", ["card", "suffix"], v=("CREDIT_CARD", value()))
    v = value()
    yield render(
        "Kartım {v}",
        ["card", "adversarial", "adv_single_digit_spaced"],
        v=("CREDIT_CARD", " ".join(v)),
    )


def _email_cases() -> Iterator[Case]:
    value = _cycle(EMAILS)
    for template, group, lang in [
        ("Teklifi {v} adresine gönder.", "context_formal", "tr"),
        ("E-posta: {v}", "context_formal", "tr"),
        ("mailim {v} oraya at", "context_colloquial", "tr"),
        ("Please reply to {v} by Friday.", "context_en", "en"),
        ("{v}", "no_context", "tr"),
        ("{v}'ya yazdım, dönüş olmadı.", "suffix", "tr"),
    ]:
        for _ in range(3):
            yield render(template, ["email", group], lang, v=("EMAIL_ADDRESS", value()))
    yield render(
        "Mail: {v}",
        ["email", "adversarial", "adv_at_word"],
        v=("EMAIL_ADDRESS", "ali.veli [at] example.com"),
    )


def _multi_cases() -> Iterator[Case]:
    tckn, phone, iban, card, mail = (
        _cycle(TCKN),
        _cycle(MOBILE),
        _cycle(TR_IBAN),
        _cycle(CARDS),
        _cycle(EMAILS),
    )
    for _ in range(10):
        yield render(
            "Müşteri bilgileri: TC {a}, cep {b}, IBAN {c}.",
            ["multi"],
            a=("TR_TCKN", tckn()),
            b=("TR_PHONE", phone_forms(phone())["trunk"]),
            c=("TR_IBAN", iban_forms(iban())["grouped"]),
        )
    for _ in range(6):
        yield render(
            "Ödeme için kart {a} kullanıldı; fatura {b} adresine, bilgi için {c}.",
            ["multi"],
            a=("CREDIT_CARD", card_forms(card())["grouped"]),
            b=("EMAIL_ADDRESS", mail()),
            c=("TR_PHONE", phone_forms(phone())["intl"]),
        )


_FILLER = (
    "Toplantıda üçüncü çeyrek satış hedefleri, yeni şube açılış takvimi ve müşteri memnuniyeti "
    "anketinin sonuçları konuşuldu. Ekip, kampanya bütçesinin ikinci yarıda dijital kanallara "
    "kaydırılmasını önerdi; lojistik tarafında teslim sürelerinin kısaltılması için iki yeni "
    "depo seçeneği değerlendirildi. "
)


def _long_cases() -> Iterator[Case]:
    tckn = _cycle(TCKN)
    for repeat in (4, 8):
        yield render(
            _FILLER * repeat
            + "Bu arada müşterinin kimlik numarası {v} olarak geçiyor. "
            + _FILLER * repeat,
            ["long"],
            v=("TR_TCKN", tckn()),
        )
    yield render(_FILLER * 10, ["long", "negative"])


def _negative_cases() -> Iterator[Case]:
    for template in [
        "Sipariş numaranız {n}, kargoya verildi.",
        "Fatura no {n} için ödeme alındı.",
        "Takip kodu {n} ile kargonuzu sorgulayabilirsiniz.",
        "Ürün seri numarası {n} garanti kapsamında.",
    ]:
        for _ in range(5):
            yield render(template, ["negative", "neg_11_digit_invalid"], n=("", _tckn_invalid()))
    for text in [
        "Toplam tutar 1.250.000 TL, vade 12.10.2026.",
        "Toplantı 12.10.2026 saat 14:30'da başlayacak.",
        "Lütfen v2.13.0 sürümünü yükleyin.",
        "Unix zaman damgası 1729171606 olarak kaydedildi.",
        "Kargo takip numarası 7340012345678 ile sorgulayın.",
        "Referans kodu TR-2026-000123 olarak geçiyor.",
        "e-Arşiv faturasında TCKN alanına 11111111111 yazılır.",
        "Oda 532, kat 12, bina B blok.",
        "Posta kodu 34710, Kadıköy.",
        "Stok kodu 4242-4242 olan ürün tükendi.",
        "Ciro geçen yıla göre %12,5 arttı; 2026 hedefi 45 milyon.",
        "Sözleşme madde 7.3.2 uyarınca fesih bildirimi 30 gün önceden yapılır.",
        "Toplantı ID: 845 1234 5678, şifre yok.",
        "ISBN 978-605-09-1234-5 numaralı kitabı sipariş et.",
        "Koordinatlar 41.0082, 28.9784.",
        "Dosya boyutu 5321234 bayt.",
        "Saat 05:32:12'de yedekleme tamamlandı.",
        "Q3 bütçesi 1.234.567,89 TL olarak onaylandı.",
        "Toplantı notlarını üç maddede özetler misin?",
        "Bu e-postayı daha resmi bir dille yeniden yazar mısın?",
        "Yeni şube açılışı için bir duyuru metni hazırla.",
        "Müşteri şikayetlerini kategorilere ayıran bir tablo oluştur.",
        "Python'da bir listeyi tersine çevirmenin en kısa yolu nedir?",
        "Summarize the quarterly report in three bullet points.",
        "Draft a polite reminder about the overdue invoice.",
        "Kimlik doğrulama adımlarını kısaca açıklar mısın?",
        "IBAN nedir, nasıl hesaplanır?",
        "Kredi kartı limit artırımı için hangi belgeler gerekir?",
        "TC kimlik numarası nasıl doğrulanır, algoritmayı anlat.",
        "Telefon numarası formatı ülkeye göre nasıl değişir?",
        "IP adresi 10.0.0.12 olan sunucuyu yeniden başlat.",
        "Çalışan sayısı 1250, ortalama yaş 34.",
        "Hesap planında 100 kasa, 102 bankalar, 120 alıcılar hesabıdır.",
        "Kampanya kodu YAZ2026 ile %20 indirim.",
    ]:
        if text.startswith("IP adresi 10.0.0.12"):
            # Relabelled 2026-10-17 (found by the test-split run): a private IP is labelled
            # IP_ADDRESS everywhere else in v2 (as Lakera flags it). Kept at the same position
            # so case ids and splits do not change.
            yield render(
                "IP adresi {v} olan sunucuyu yeniden başlat.",
                ["ip", "ip_private"],
                v=("IP_ADDRESS", "10.0.0.12"),
            )
            continue
        yield render(text, ["negative", "neg_clean"])
    for template in ["Seri no {n}", "Ürün kodu {n}"]:
        yield render(template, ["negative", "neg_luhn_invalid"], n=("", "4242424242424241"))
    for template, n in [
        ("Fatura kalemi kodu {n}", "5991234567"),
        ("Lot numarası {n}", "5991234567"),
        ("Poliçe no {n} yenilendi.", "4001234567"),
        ("Müşteri numarası {n} ile giriş yapın.", "7123456789"),
        ("Abone no: {n}", "6001234567"),
        ("Sözleşme {n} imzalandı.", "1002003004"),
    ]:
        yield render(template, ["negative", "neg_10_digit_non_mobile"], n=("", n))


def _vkn_cases() -> Iterator[Case]:
    vkn = _cycle(VKN)
    for template, lang in [
        ("Şirketin vergi kimlik numarası {v}.", "tr"),
        ("VKN: {v}", "tr"),
        ("Vergi no {v} olan tedarikçiye fatura kes.", "tr"),
        ("Tax ID {v} for the supplier.", "en"),
        ("vergi numaram {v}", "tr"),
        ("Fatura bilgileri: Örnek A.Ş., VKN {v}, Kadıköy V.D.", "tr"),
    ]:
        yield render(template, ["vkn"], lang, v=("TR_VKN", vkn()))


# ---------------------------------------------------- v2: Lakera parity, Türkiye, Europe
# Generator-local validators (independent of the engine's validators, so the dataset can check
# them). Canonical published examples are asserted against them at import time.

_ES_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def _es_dni(number: int) -> str:
    return f"{number:08d}{_ES_LETTERS[number % 23]}"


def _es_nie(prefix: str, number: int) -> str:
    return f"{prefix}{number:07d}{_ES_LETTERS[('XYZ'.index(prefix) * 10**7 + number) % 23]}"


_IT_ODD = dict(
    zip(
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        [
            1,
            0,
            5,
            7,
            9,
            13,
            15,
            17,
            19,
            21,
            1,
            0,
            5,
            7,
            9,
            13,
            15,
            17,
            19,
            21,
            2,
            4,
            18,
            20,
            11,
            3,
            6,
            8,
            12,
            14,
            16,
            10,
            22,
            25,
            24,
            23,
        ],
        strict=True,
    )
)


def _it_cf(first15: str) -> str:
    total = 0
    for i, ch in enumerate(first15, start=1):
        if i % 2:
            total += _IT_ODD[ch]
        else:
            total += int(ch) if ch.isdigit() else ord(ch) - ord("A")
    return first15 + chr(ord("A") + total % 26)


def _nl_bsn_valid(value: str) -> bool:
    weights = [9, 8, 7, 6, 5, 4, 3, 2, -1]
    return (
        len(value) == 9 and sum(int(c) * w for c, w in zip(value, weights, strict=True)) % 11 == 0
    )


def _pl_pesel(first10: str) -> str:
    weights = [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]
    total = sum(int(c) * w for c, w in zip(first10, weights, strict=True))
    return first10 + str((10 - total % 10) % 10)


def _de_tax_id(first10: str) -> str:
    product = 10
    for ch in first10:
        total = (int(ch) + product) % 10 or 10
        product = (total * 2) % 11
    check = 11 - product
    return first10 + ("0" if check == 10 else str(check))


def _fr_nir(first13: str) -> str:
    return f"{first13}{97 - int(first13) % 97:02d}"


assert _es_dni(12345678) == "12345678Z"
assert _es_nie("X", 1234567) == "X1234567L"
assert _it_cf("RSSMRA85T10A562") == "RSSMRA85T10A562S"
assert _nl_bsn_valid("111222333")
assert _pl_pesel("4405140135") == "44051401359"

ES_DNI = [_es_dni(n) for n in (12345678, 23456789, 34567890, 45678901)]
ES_NIE = [_es_nie("X", 1234567), _es_nie("Y", 2345678), _es_nie("Z", 3456789)]
IT_CF = [_it_cf(p) for p in ("RSSMRA85T10A562", "BNCGPP80A01F205", "VRDLCU90E45H501")]
NL_BSN = [v for v in (f"{n:09d}" for n in range(111222333, 111232333)) if _nl_bsn_valid(v)][:4]
PL_PESEL = [_pl_pesel(p) for p in ("4405140135", "8501011234", "9202153456")]
DE_TAX_ID = [_de_tax_id(p) for p in ("8609574271", "1234567891", "4715632905")]
FR_NIR = [_fr_nir(p) for p in ("1850775115003", "2920369123045", "1790213055012")]
UK_NINO = ["JG103759A", "HN456789B", "AB123456C", "PR904213D"]

# US SSN: patterned areas/groups/serials, all outside the invalid ranges (000, 666, 9xx, 00, 0000)
US_SSN = ["234-56-7890", "345-67-8901", "123-45-6780", "456-78-9012", "512-34-0921", "078-23-4561"]
US_PHONE = [
    "(415) 555-0132",
    "+1 212 555 0147",
    "415-555-0199",
    "+1-646-555-0110",
]  # 555-01xx fictional
EU_PHONE = [
    ("+44 20 7946 0958", "uk"),  # Ofcom drama range
    ("07700 900123", "uk"),
    ("+33 1 99 00 12 34", "fr"),  # ARCEP fiction range
    ("06 39 98 12 34", "fr"),
    ("+49 30 12345678", "de"),
    ("+34 912 345 678", "es"),
    ("+39 06 1234 5678", "it"),
    ("+31 20 123 4567", "nl"),
]
IP_PUBLIC = ["85.105.12.34", "176.88.1.20", "31.145.7.8", "2a02:ff0:3301:12::1", "194.27.50.9"]
IP_PRIVATE = ["192.168.1.10", "10.20.30.40", "172.16.5.4"]
UNKNOWN_LINKS = [
    "https://secure-hesap-dogrula.xyz/login",
    "http://paypa1-support.top/verify?id=8812",
    "www.kampanya-firsati-2026.click/odul",
    "https://invoice-docs-share.site/f/abc123",
    "https://e-devlet-giris.online/tr",
]
POPULAR_LINKS = [
    "https://www.google.com/search?q=hava+durumu",
    "https://github.com/UMAI-Solutions",
    "https://tr.wikipedia.org/wiki/Istanbul",
    "https://www.youtube.com/watch?v=abc123",
]
ADDRESSES = [
    ("742 Evergreen Terrace, Springfield, OR 97477", "us"),
    ("1234 Elm Street, Apt 5B, Austin, TX 78701", "us"),
    ("88 Maple Avenue, Boston, MA 02116", "us"),
    ("Caferağa Mah. Moda Cad. No: 12 D: 4 Kadıköy/İstanbul", "tr"),
    ("Kızılay Mah. Atatürk Bulvarı No:45 Çankaya/Ankara", "tr"),
    ("Kıbrıs Şehitleri Cad. No:120, 35220 Konak/İzmir", "tr"),
    ("Bağdat Caddesi No: 301 Daire: 7, 34728 Kadıköy, İstanbul", "tr"),
    ("Hauptstraße 5, 10115 Berlin", "eu"),
    ("12 Rue de la Paix, 75002 Paris", "eu"),
    ("Calle Mayor 10, 28013 Madrid", "eu"),
    ("Via Roma 25, 00184 Roma", "eu"),
    ("221B Baker Street, London NW1 6XE", "eu"),
]
EU_NAMES = [
    "Hans Müller",
    "Marie Dubois",
    "Giulia Rossi",
    "Jan de Vries",
    "Anna Kowalska",
    "Carlos García",
    "James O'Brien",
    "Sophie Martin",
]
TR_PLATES = ["34 ABC 123", "06 A 12345", "35 KL 4567", "16 BR 902"]


def _us_cases() -> Iterator[Case]:
    ssn = _cycle(US_SSN)
    for template in [
        "SSN: {v}",
        "My social security number is {v}.",
        "Employee SSN {v} is missing on the W-2 form.",
        "{v}",
        "Müşterinin ABD sosyal güvenlik numarası {v}.",
    ]:
        yield render(
            template,
            ["ssn", "context" if template != "{v}" else "no_context"],
            "en",
            v=("US_SSN", ssn()),
        )
    yield render("SSN {v}", ["ssn", "format_spaced"], "en", v=("US_SSN", "345 67 8901"))
    yield render(
        "social security no {v}", ["ssn", "format_compact"], "en", v=("US_SSN", "456789012")
    )
    for text in [
        "SSN 000-12-3456 is not a valid number.",
        "Test SSN 666-45-1234 in the sample.",
        "Order 900-12-3456 shipped.",
    ]:
        yield render(text, ["negative", "neg_ssn_invalid"], "en")
    phone = _cycle(US_PHONE)
    for template in ["Call me at {v}.", "US office phone: {v}", "Müşterinin ABD numarası {v}"]:
        for _ in range(2):
            yield render(template, ["intl_phone", "phone_us"], "en", v=("PHONE_NUMBER", phone()))


def _eu_phone_cases() -> Iterator[Case]:
    templates = {
        "uk": "Call our London office on {v}.",
        "fr": "Appelez-moi au {v}.",
        "de": "Rufen Sie mich unter {v} an.",
        "es": "Llámame al {v}.",
        "it": "Chiamami al {v}.",
        "nl": "Bel me op {v}.",
    }
    for number, country in EU_PHONE:
        yield render(
            templates[country], ["intl_phone", f"phone_{country}"], "eu", v=("PHONE_NUMBER", number)
        )
        yield render(
            "Avrupa ofisinin telefonu {v}",
            ["intl_phone", f"phone_{country}"],
            "tr",
            v=("PHONE_NUMBER", number),
        )


def _ip_cases() -> Iterator[Case]:
    public = _cycle(IP_PUBLIC)
    for template, lang in [
        ("Sunucunun IP adresi {v}.", "tr"),
        ("Login from {v} was blocked.", "en"),
        ("Müşteri {v} adresinden bağlandı.", "tr"),
        ("{v}", "tr"),
        ("Allowlist this IP: {v}", "en"),
        ("IPv6 adresi: {v}", "tr"),
    ]:
        yield render(template, ["ip", "ip_public"], lang, v=("IP_ADDRESS", public()))
    for template, value in zip(
        ["Ofis yazıcısı {v} adresinde.", "Internal host {v} is down.", "VPN sonrası IP {v} oldu."],
        IP_PRIVATE,
        strict=True,
    ):
        yield render(template, ["ip", "ip_private"], v=("IP_ADDRESS", value))
    for text in [
        "Yerel test için 127.0.0.1 kullan.",
        "DNS sunucusu olarak 8.8.8.8 ayarlı.",
        "Alt ağ maskesi 255.255.255.0 olmalı.",
        "Sürüm 10.4.2 yayınlandı.",
        "Multicast grubu 224.0.0.1 dinleniyor.",
        "Bölüm 3.2.1'e bakın.",
    ]:
        yield render(text, ["negative", "neg_ip_special"])


def _link_cases() -> Iterator[Case]:
    link = _cycle(UNKNOWN_LINKS)
    for template, lang in [
        ("Hesabınızı doğrulamak için {v} adresine girin.", "tr"),
        ("Click {v} to reset your password.", "en"),
        ("Detaylar burada: {v}", "tr"),
        ("{v}", "tr"),
        ("Faturanızı görüntüleyin: {v}", "tr"),
    ]:
        yield render(template, ["link", "link_unknown"], lang, v=("UNKNOWN_LINK", link()))
    for template, value in zip(
        ["Kaynak: {v}", "Repo: {v}", "Bkz. {v}", "Video: {v}"], POPULAR_LINKS, strict=True
    ):
        yield render(template, ["negative", "neg_popular_link"], v=("", value))


def _address_cases() -> Iterator[Case]:
    contexts = {
        "us": ["Ship it to {v}.", "Home address: {v}"],
        "tr": ["Teslimat adresi: {v}", "Müşteri {v} adresinde oturuyor."],
        "eu": ["Lieferadresse: {v}", "Send the contract to {v}."],
    }
    for i, (address, region) in enumerate(ADDRESSES):
        template = contexts[region][i % 2]
        yield render(
            template,
            ["address", f"address_{region}"],
            {"us": "en", "tr": "tr", "eu": "eu"}[region],
            v=("ADDRESS", address),
        )
    for text in [
        "Toplantı İstanbul'da yapılacak.",
        "Berlin ofisi pazartesi kapalı.",
        "Texas'taki müşterilerimiz için kampanya.",
        "Kadıköy şubesi yeni açıldı.",
    ]:
        yield render(text, ["negative", "neg_city_only"])


def _person_cases() -> Iterator[Case]:
    name = _cycle(NAMES + EU_NAMES)
    for template, lang in [
        ("Müşteri adı {v}, randevusunu yarına al.", "tr"),
        ("{v} ile görüşme notlarını özetle.", "tr"),
        ("Sayın {v}, başvurunuz alınmıştır.", "tr"),
        ("Please schedule a call with {v}.", "en"),
        ("Dosyayı {v}'a gönder.", "tr"),
        ("Hasta {v} kontrole gelecek.", "tr"),
        ("Bordroda {v} için fazla mesai eksik.", "tr"),
        ("Contract signed by {v} on Monday.", "en"),
        ("Le dossier de {v} est complet.", "eu"),
        ("Bitte {v} zurückrufen.", "eu"),
        ("Invoice addressed to {v}.", "en"),
        ("{v} izin talebinde bulundu.", "tr"),
    ]:
        yield render(template, ["person"], lang, v=("PERSON", name()))
    for text in [
        "Ahmet toplantıya geldi mi?",
        "John Doe örnek kullanıcı adıdır.",
        "Mehmet Bey aradı, dönüş bekliyor.",
        "Ayşe'ye sunumu ilet.",
        "Jane Doe is a placeholder name.",
    ]:
        yield render(text, ["negative", "neg_single_or_placeholder_name"])


def _eu_id_cases() -> Iterator[Case]:
    groups = [
        ("ES_NIF", ES_DNI, ["DNI: {v}", "Mi DNI es {v}.", "Müşterinin İspanyol kimliği (DNI) {v}"]),
        ("ES_NIE", ES_NIE, ["NIE {v}", "Número de identidad de extranjero: {v}"]),
        ("IT_FISCAL_CODE", IT_CF, ["Codice fiscale: {v}", "Il mio codice fiscale è {v}."]),
        ("NL_BSN", NL_BSN, ["BSN: {v}", "Mijn burgerservicenummer is {v}."]),
        ("PL_PESEL", PL_PESEL, ["PESEL: {v}", "Mój numer PESEL to {v}."]),
        ("DE_TAX_ID", DE_TAX_ID, ["Steuer-ID: {v}", "Meine Steueridentifikationsnummer ist {v}."]),
        ("FR_NIR", FR_NIR, ["Numéro de sécurité sociale : {v}", "NIR {v}"]),
        ("UK_NINO", UK_NINO, ["National Insurance number: {v}", "NI number {v}"]),
    ]
    for entity_type, values, templates in groups:
        value = _cycle(values)
        for template in templates + templates[:1]:
            yield render(template, ["eu_id", entity_type.lower()], "eu", v=(entity_type, value()))
    yield render(
        "NINO {v}", ["eu_id", "uk_nino", "format_spaced"], "eu", v=("UK_NINO", "JG 10 37 59 A")
    )
    yield render(
        "NIR {v}",
        ["eu_id", "fr_nir", "format_spaced"],
        "eu",
        v=("FR_NIR", "1 85 07 75 115 003 " + FR_NIR[0][-2:]),
    )
    for text in [
        "Referans 123456789 ile kayıtlı.",
        "Bestelnummer 987654321 is verzonden.",
        "Codice prodotto ABCDEF12G34H567Z non valido.",
    ]:
        yield render(text, ["negative", "neg_eu_id_shape"])


def _tr_extra_cases() -> Iterator[Case]:
    plate = _cycle(TR_PLATES)
    for template in [
        "Aracın plakası {v}.",
        "{v} plakalı araç otoparkta.",
        "Plaka: {v}",
        "Kaza tutanağında {v} yazıyor.",
    ]:
        yield render(template, ["plate"], v=("TR_PLATE", plate()))
    for text in ["Toplantı 12 eki 2024'te yapıldı.", "Paket 10 kg 500 g geldi."]:
        yield render(text, ["negative", "neg_plate_shape"])
    for template, value in [
        ("Mail: {v}", "ali.veli(at)example(dot)com"),
        ("e-postam {v}", "ali.veli at example nokta com"),
    ]:
        yield render(
            template, ["email", "adversarial", "adv_obfuscated"], v=("EMAIL_ADDRESS", value)
        )
    for template, value in [
        ("Diners kartım {v}", "3056 930902 5904"),
        ("Maestro card {v}", _luhn_complete("675964982643845123")),
    ]:
        yield render(template, ["card", "card_length"], v=("CREDIT_CARD", value))


assert _de_tax_id("8609574271") == "86095742719"


# ---------------------------------------------------- v2 expansion: ≥ 20 cases per catalog type
# Values and phrasings written independently of the recognizers; some names and addresses are
# deliberately outside the PERSON gazetteer / address patterns so recall is not flattered.

ES_DNI_MORE = [_es_dni(n) for n in (56789012, 67890123, 78901234, 89012345, 11223344, 22334455)]
ES_NIE_MORE = [
    _es_nie(p, n)
    for p, n in (("X", 7654321), ("Y", 1112223), ("Z", 9876543), ("X", 2468135), ("Y", 1357924))
]
IT_CF_MORE = [
    _it_cf(p)
    for p in (
        "FRRLCU75C12F205",
        "MNTGLI88D52H501",
        "BRNMRC70A01L219",
        "CSTFNC92M41F839",
        "GRCPLA65B15A944",
        "RSSLRA99T45H501",
    )
]
NL_BSN_MORE = [v for v in (f"{n:09d}" for n in range(123456782, 123556782)) if _nl_bsn_valid(v)][:6]
PL_PESEL_MORE = [
    _pl_pesel(p)
    for p in ("9001011234", "8512312345", "0221035678", "7707074321", "6503158765", "9910109876")
]


def _de_first10(seed: int) -> str:
    """Ten digits for a German tax ID: first not 0, exactly one digit twice, one missing."""

    rng = random.Random(seed)
    while True:
        digits = rng.sample("0123456789", 9)
        digits.insert(rng.randrange(1, 10), rng.choice(digits))
        if digits[0] != "0":
            return "".join(digits)


DE_TAX_ID_MORE = [_de_tax_id(_de_first10(seed)) for seed in range(6)]
FR_NIR_MORE = [
    _fr_nir(p)
    for p in (
        "1680592120034",
        "2851275056117",
        "1931131555098",
        "2770469383021",
        "1550144109007",
        "2010533063142",
    )
]
UK_NINO_MORE = ["JA123456B", "HJ987654C", "PE246810A", "SK135792D", "WL112233B", "AE654321C"]
SSN_MORE = [
    "234-11-5678",
    "345-22-6789",
    "456-33-7890",
    "567-44-8901",
    "612-55-9012",
    "701-66-1234",
    "389-77-2345",
    "142-88-3456",
    "275-99-4567",
    "513-10-5671",
    "622-21-6782",
    "731-32-7893",
    "248-43-8904",
]
VKN_MORE = [
    _vkn_check(f"{d}{n:08d}")
    for d, n in (
        (1, 23456788),
        (3, 45678901),
        (5, 67890123),
        (7, 89012345),
        (2, 13579246),
        (4, 24681357),
        (6, 11223344),
        (8, 55667788),
        (9, 99887766),
        (1, 10293847),
        (3, 56473829),
        (5, 19283746),
        (7, 64738291),
        (2, 37465819),
    )
]
PLATES_MORE = [
    "01 ADN 34",
    "07 KV 1234",
    "41 TR 567",
    "61 GH 4321",
    "55 S 12345",
    "42 KN 908",
    "27 GT 345",
    "33 MRS 12",
    "26 ES 2626",
    "10 BK 910",
    "48 MG 4848",
    "59 TK 590",
    "20 DN 202",
    "38 KY 3838",
    "21 DY 2121",
    "65 VN 650",
]
IP_MORE = [
    "78.186.4.21",
    "88.255.97.3",
    "212.156.70.12",
    "95.70.140.8",
    "46.196.33.200",
    "2a00:1d58:41::7",
    "2001:4c80:5:1::12",
    "185.86.152.40",
    "151.250.6.77",
    "100.70.8.9",
    "192.168.10.250",
    "fd12:3456:789a::1",
]
LINKS_MORE = [
    "https://hesap-guncelle-tr.top/form",
    "http://kargo-takip-ptt.online/sorgula?no=123",
    "https://bit-odeme-iade.site/claim",
    "www.ucretsiz-iphone-kazan.click",
    "https://microsoft-login-verify.info/auth",
    "https://docs-share-preview.app/d/77",
    "http://192-168-update.net/fw",
    "https://garanti-bonus-puan.xyz/basvur",
    "https://ziraat-mobil-giris.icu/",
    "https://wallet-recovery-help.live/start",
]
NAMES_OFF_LIST = [
    "Doruk Ersoy",
    "Sıla Uçar",
    "Tarkan Özgür",
    "İlkin Sarp",
    "Ingrid Lindqvist",
    "Wojciech Nowak",
    "Siobhán Kelly",
    "Bartholomew Finch",
    "Yusra Al-Amin",
    "Kerime Tunalı",
]
ADDRESSES_MORE = [
    ("Barbaros Bulvarı No: 145 Beşiktaş/İstanbul", "tr"),
    ("Cumhuriyet Mah. 1203. Sokak No:7 D:2 Konak/İzmir", "tr"),
    ("Esentepe Mah. Büyükdere Cad. No: 199 Şişli/İstanbul", "tr"),
    ("Atatürk Cad. No:33 Kat:3, 06420 Çankaya/Ankara", "tr"),
    ("500 Market Street, San Francisco, CA 94105", "us"),
    ("77 Ocean Drive, Miami, FL 33139", "us"),
    ("Kurfürstendamm 21, 10719 Berlin", "eu"),
    ("Damstraat 12, 1012 JM Amsterdam", "eu"),
    ("Piazza Navona 3, 00186 Roma", "eu"),
    ("8 Avenue des Champs-Élysées, 75008 Paris", "eu"),
    ("Flat 3, 14 Kensington Gardens, London W8 4PX", "eu"),
]
SECRETS = [
    ("API_KEY", "AKIAIOSFODNN7EXAMPLE"),  # AWS documentation example key
    ("API_KEY", "AKIAEXAMPLE000000001"),
    ("API_KEY", "AKIAEXAMPLE000000002"),
    (
        "JWT",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    ),  # jwt.io example
    ("CONNECTION_STRING", "postgres://app_user:S3cretPass@db.example.com:5432/orders"),
    ("CONNECTION_STRING", "mongodb://admin:ExamplePass123@mongo.example.org:27017/crm"),
    ("CONNECTION_STRING", "mysql://reporter:R3port!ng@10.0.0.5/analytics"),
    (
        "PRIVATE_KEY",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAexampleexampleexampleexample\n"
        "-----END RSA PRIVATE KEY-----",
    ),
    (
        "PRIVATE_KEY",
        "-----BEGIN PRIVATE KEY-----\nMIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHexample\n"
        "-----END PRIVATE KEY-----",
    ),
]


def _expanded_cases() -> Iterator[Case]:
    def many(
        entity: str, tag: str, values: list[str], templates: list[tuple[str, str]], count: int = 16
    ) -> Iterator[Case]:
        value, template = _cycle(values), _cycle(templates)
        for _ in range(count):
            text, lang = template()
            yield render(text, [tag], lang, v=(entity, value()))

    yield from many(
        "US_SSN",
        "ssn",
        SSN_MORE,
        [
            ("Applicant SSN: {v}", "en"),
            ("SSN {v} on file.", "en"),
            ("Please verify social security number {v}.", "en"),
            ("{v} is the employee's SSN.", "en"),
            ("Çalışanın SSN numarası {v}", "tr"),
            ("Tax form W-2 lists {v}.", "en"),
            ("{v}", "en"),
            ("Customer record: name withheld, SSN {v}", "en"),
        ],
    )
    yield from many(
        "TR_VKN",
        "vkn",
        VKN_MORE,
        [
            ("Firmanın vergi numarası {v}.", "tr"),
            ("VKN {v}", "tr"),
            ("Vergi kimlik no: {v}", "tr"),
            ("Kadıköy V.D. {v}", "tr"),
            ("Tax number {v} for the Turkish supplier.", "en"),
            ("Vergi dairesi Beşiktaş, no {v}", "tr"),
            ("Faturada vergi no {v} yazıyor.", "tr"),
        ],
        count=14,
    )
    yield from many(
        "TR_PLATE",
        "plate",
        PLATES_MORE,
        [
            ("Aracın plakası {v}.", "tr"),
            ("{v} plakalı araç kaza yaptı.", "tr"),
            ("Otoparktaki {v} plakalı aracı çekin.", "tr"),
            ("Ruhsatta plaka {v} görünüyor.", "tr"),
            ("Plate number {v} was photographed.", "en"),
            ("Trafik cezası {v} plakasına kesildi.", "tr"),
        ],
    )
    yield from many(
        "IP_ADDRESS",
        "ip",
        IP_MORE,
        [
            ("Saldırı {v} adresinden geldi.", "tr"),
            ("Block IP {v} at the firewall.", "en"),
            ("Kullanıcı son olarak {v} IP'sinden giriş yaptı.", "tr"),
            ("Origin: {v}", "en"),
            ("VPN çıkış adresi {v}", "tr"),
        ],
        count=12,
    )
    yield from many(
        "UNKNOWN_LINK",
        "link",
        LINKS_MORE,
        [
            ("Ödülünüzü almak için {v} adresine tıklayın.", "tr"),
            ("Verify here: {v}", "en"),
            ("Kargo bilginiz: {v}", "tr"),
            ("Belgeyi buradan indir {v}", "tr"),
            ("Your account is locked, visit {v} now.", "en"),
        ],
        count=15,
    )
    yield from many(
        "PERSON",
        "person",
        NAMES_OFF_LIST + NAMES[:5],
        [
            ("Toplantıya {v} katılacak.", "tr"),
            ("Teklif {v} tarafından onaylandı.", "tr"),
            ("Please email the report to {v}.", "en"),
            ("Kiracı: {v}", "tr"),
            ("Signed: {v}", "en"),
            ("Öğrenci {v} devamsızlıktan kaldı.", "tr"),
        ],
        count=14,
    )
    for address, region in ADDRESSES_MORE:
        template = {"tr": "Adres: {v}", "us": "Mailing address: {v}", "eu": "Address: {v}"}[region]
        yield render(
            template,
            ["address", f"address_{region}"],
            {"us": "en", "tr": "tr", "eu": "eu"}[region],
            v=("ADDRESS", address),
        )
    for entity, values, templates in [
        ("ES_NIF", ES_DNI_MORE, [("DNI {v}", "eu"), ("Su DNI es {v}.", "eu"), ("NIF: {v}", "eu")]),
        ("ES_NIE", ES_NIE_MORE, [("NIE: {v}", "eu"), ("Tarjeta de residencia, NIE {v}", "eu")]),
        (
            "IT_FISCAL_CODE",
            IT_CF_MORE,
            [
                ("Codice fiscale {v}", "eu"),
                ("CF: {v}", "eu"),
                ("Il codice fiscale del cliente è {v}.", "eu"),
            ],
        ),
        ("NL_BSN", NL_BSN_MORE, [("BSN {v}", "eu"), ("Burgerservicenummer: {v}", "eu")]),
        ("PL_PESEL", PL_PESEL_MORE, [("PESEL {v}", "eu"), ("Numer PESEL: {v}", "eu")]),
        (
            "DE_TAX_ID",
            DE_TAX_ID_MORE,
            [("Steuer-ID {v}", "eu"), ("Ihre Steueridentifikationsnummer: {v}", "eu")],
        ),
        ("FR_NIR", FR_NIR_MORE, [("NIR : {v}", "eu"), ("Numéro de sécurité sociale {v}", "eu")]),
        ("UK_NINO", UK_NINO_MORE, [("NI number: {v}", "eu"), ("National Insurance No. {v}", "eu")]),
    ]:
        yield from many(entity, "eu_id", values, templates, count=15)
    secret = _cycle(SECRETS)
    for template, lang in [
        ("Config: aws_access_key_id = {v}", "en"),
        ("Bu anahtarı kullan: {v}", "tr"),
        ("token={v}", "en"),
        ("DATABASE_URL={v}", "en"),
        ("Anahtar dosyası:\n{v}", "tr"),
        ("Here is the key {v} for prod.", "en"),
        ("Bağlantı: {v}", "tr"),
        ("export SECRET={v}", "en"),
        ("Sunucu kimlik bilgisi {v}", "tr"),
    ] * 3:
        entity, value = secret()
        yield render(template, ["secret", entity.lower()], lang, v=(entity, value))
    for template, value, lang in [
        ("password: {v}", "Hunter2!x", "en"),
        ("Şifrem: {v}", "Gizli.2026", "tr"),
        ("DB_PASSWORD={v}", "pr0d-Secret-77", "en"),
        ("parola = {v}", "Ankara#1923", "tr"),
        ("Wi-Fi şifresi {v} olarak değişti", "Misafir2026!", "tr"),
        ("pwd={v}", "Example#Pass9", "en"),
        ("The admin password is {v}", "Adm1n-Example", "en"),
        ("şifre: {v}", "Kedi.Kopek.45", "tr"),
        ("passwd = {v}", "r00t-Example", "en"),
        ("Sisteme giriş şifresi: {v}", "Yaz2026*Tatil", "tr"),
    ] * 2:
        yield render(template, ["secret", "password"], lang, v=("PASSWORD", value))
    for text in [
        "Şifre en az 8 karakter olmalı.",
        "Password reset link has been sent.",
        "API anahtarı nedir, nasıl oluşturulur?",
        "Bearer token ile kimlik doğrulama yapılır.",
        "-----BEGIN PUBLIC KEY-----",
        "postgres://db.example.com:5432/app adresine bağlan.",
        "Şifremi unuttum, nasıl sıfırlarım?",
        "Use the access key from the vault.",
        "JWT nasıl çalışır, kısaca anlat.",
        "Parola politikası 90 günde bir değişim ister.",
    ]:
        yield render(text, ["negative", "neg_secret_shape"])
    for text in [
        "Ahmet ve Mehmet toplantıya geldi.",
        "Elif yarın izinli.",
        "Hasan Bey ile görüştüm.",
        "Grace period is 30 days.",
        "Mark the invoice as paid.",
        "Can Bank hesabı açıldı mı?",
        "Rose garden open Monday.",
    ]:
        yield render(text, ["negative", "neg_name_shape"])


GROUPS = (
    _tckn_cases,
    _phone_cases,
    _iban_cases,
    _card_cases,
    _email_cases,
    _multi_cases,
    _long_cases,
    _negative_cases,
    _vkn_cases,
    _us_cases,
    _eu_phone_cases,
    _ip_cases,
    _link_cases,
    _address_cases,
    _person_cases,
    _eu_id_cases,
    _tr_extra_cases,
    _expanded_cases,
)


def _split(case_id: str) -> str:
    bucket = int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 10
    return "test" if bucket < TEST_SHARE else "dev"


def build() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for group in GROUPS:
        for case in group():
            case_id = f"l0-{len(records) + 1:04d}"
            records.append(
                {
                    "case_id": case_id,
                    "split": _split(case_id),
                    "lang": case.lang,
                    "source": "generated",
                    "tags": case.tags,
                    "text": case.text,
                    "expected": case.expected,
                }
            )
    return records


def main() -> None:
    records = build()
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    with DATASET.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    splits = {s: sum(r["split"] == s for r in records) for s in ("dev", "test")}
    print(f"wrote {len(records)} cases to {DATASET} ({splits})")


if __name__ == "__main__":
    main()
