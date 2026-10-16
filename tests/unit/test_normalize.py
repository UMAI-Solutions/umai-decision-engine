import unicodedata

from umai_decision_engine.l0.normalize import match_fold, normalize, tr_casefold


def test_zero_width_removed_and_offsets_map_back() -> None:
    original = "TC 1000​0000146 ok"
    norm = normalize(original)
    assert norm.text == "TC 10000000146 ok"
    start = norm.text.index("1")
    s, e = norm.to_original(start, start + 11)
    assert original[s:e] == "1000​0000146"


def test_full_width_digits_become_ascii() -> None:
    original = "１０００００００１４６"
    norm = normalize(original)
    assert norm.text == "10000000146"
    assert norm.to_original(0, 11) == (0, 11)


def test_nfd_turkish_letters_compose() -> None:
    original = unicodedata.normalize("NFD", "kişi şube")
    assert len(original) > len("kişi şube")
    norm = normalize(original)
    assert norm.text == "kişi şube"
    s, e = norm.to_original(0, 4)
    assert unicodedata.normalize("NFC", original[s:e]) == "kişi"


def test_turkish_casefold() -> None:
    assert "KİMLİK".lower() != "kimlik"  # the reason this helper exists
    assert tr_casefold("KİMLİK") == "kimlik"
    assert tr_casefold("IŞIK") == "ışık"
    assert match_fold("KİMLİK") == "kimlik"
    assert match_fold("IBAN") == "iban"
