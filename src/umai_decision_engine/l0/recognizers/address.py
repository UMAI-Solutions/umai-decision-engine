"""Street addresses (`ADDRESS`): Turkish, US and European formats.

Deterministic patterns anchored on street-type words and house numbers (Lakera's address
detector is US-only, docs/reference/lakera-guard.md). A city or state on its own is never an
address. An NER model may add recall later (open question Q8); these patterns are the
on-prem, zero-model baseline.
"""

from __future__ import annotations

from umai_decision_engine.l0.recognizers.base import PatternSpec, RecognizerSpec

_W = r"\p{Lu}[\p{L}\-]+"  # a capitalised word (Unicode letters)
_PLACE = rf"{_W}(?:\s{_W}){{0,2}}"

# Türkiye: [Mahalle] [Cadde/Sokak/Bulvar] No: n [D/Daire/Kat: n] [, PK] [İlçe/İl | İlçe, İl]
_TR_STREET = r"(?:Cad\.?|Caddesi|Cd\.|Sok\.?|Sokak|Sokağı|Sk\.|Bulvarı|Blv\.?|Bulv\.)"
_TR_ADDRESS = (
    rf"(?<![\p{{L}}])(?:{_PLACE}\s(?:Mah\.?|Mahallesi|Mh\.)\s*,?\s*)?"
    rf"(?:(?:{_PLACE}|[0-9]{{1,5}}\.?)\s{_TR_STREET}\s*,?\s*)"
    r"(?:No\s*:?\s*[0-9]+[A-Za-z]?(?:/[0-9]+)?)"
    r"(?:\s*,?\s*(?:D|Daire|Kat|Blok)\s*:?\s*[0-9A-Za-z]+)*"
    rf"(?:\s*,?\s*(?:[0-9]{{5}}\s+)?{_W}\s*[/,]\s*{_W})?"
)

# United States: number + street name + suffix [, unit] [, City] [, ST] [ZIP]
_US_SUFFIX = (
    r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Lane|Ln\.?|Drive|Dr\.?|"
    r"Court|Ct\.?|Terrace|Ter\.?|Place|Pl\.?|Way|Parkway|Pkwy\.?|Circle|Cir\.?|Highway|Hwy\.?)"
)
_US_ADDRESS = (
    rf"(?<![\w])[0-9]{{1,6}}\s(?:[A-Z][a-z]+\s){{1,4}}{_US_SUFFIX}"
    r"(?:,?\s(?:Apt|Apartment|Suite|Ste|Unit|#)\.?\s?[\w\-]+)?"
    r"(?:,\s[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+){0,2})?"
    r"(?:,\s[A-Z]{2})?(?:\s[0-9]{5}(?:-[0-9]{4})?)?"
)

# United Kingdom: number[letter] + street + type, [town,] postcode
_UK_ADDRESS = (
    r"(?<![\w])[0-9]{1,4}[A-Z]?\s(?:[A-Z][a-z]+\s){1,3}"
    r"(?:Street|Road|Lane|Avenue|Close|Way|Place|Square|Gardens|Crescent|Drive|Terrace|Court|Hill|Row|Mews)"
    r"(?:,\s[A-Z][a-z]+)*,?\s[A-Z]{1,2}[0-9][A-Z0-9]?\s?[0-9][A-Z]{2}(?![\w])"
)

# Germany / Austria / Switzerland: Name-straße|weg|platz… n[a], PLZ Stadt
_DE_ADDRESS = (
    r"(?<![\p{L}])\p{Lu}[\p{L}\-]*(?:straße|strasse|str\.|weg|platz|allee|gasse|ring|damm|ufer)"
    rf"\s[0-9]{{1,4}}[a-z]?,\s*[0-9]{{4,5}}\s{_W}"
)

# France: n [bis|ter] rue|avenue|boulevard… name, CP Ville
_FR_ADDRESS = (
    r"(?i:(?<![\w])[0-9]{1,4}(?:\s?(?:bis|ter))?,?\s(?:rue|avenue|av\.|boulevard|bd|place|allée|"
    r"chemin|impasse|quai|cours|route)\s)(?:[\p{L}'’\-]+\s){0,5}[\p{L}'’\-]+,\s*[0-9]{5}\s"
    rf"{_W}"
)

# Spain / Italy / Portugal / Netherlands: Street-type Name n, CP City
_SOUTH_ADDRESS = (
    r"(?<![\p{L}])(?:Calle|C/|Avenida|Avda\.?|Plaza|Paseo|Carrer|Via|Viale|Piazza|Corso|Largo|"
    r"Rua|Avenida)\s(?:(?:de|del|la|dei|della|di|do|da)\s)?(?:\p{Lu}[\p{L}]+\s?){1,4},?\s*"
    rf"[0-9]{{1,4}}[a-z]?,\s*[0-9]{{4,5}}(?:-[0-9]{{3}})?\s{_W}"
)
_NL_ADDRESS = (
    r"(?<![\p{L}])\p{Lu}[\p{L}]+(?:straat|weg|plein|gracht|laan|kade|dijk)\s[0-9]{1,4}[a-z]?,"
    rf"\s*[0-9]{{4}}\s?[A-Z]{{2}}\s{_W}"
)

ADDRESS = RecognizerSpec(
    entity="ADDRESS",
    patterns=(
        PatternSpec("address_tr", _TR_ADDRESS, 0.65),
        PatternSpec("address_us", _US_ADDRESS, 0.6),
        PatternSpec("address_uk", _UK_ADDRESS, 0.65),
        PatternSpec("address_de", _DE_ADDRESS, 0.65),
        PatternSpec("address_fr", _FR_ADDRESS, 0.65),
        PatternSpec("address_south", _SOUTH_ADDRESS, 0.65),
        PatternSpec("address_nl", _NL_ADDRESS, 0.65),
    ),
    canonical=lambda value: " ".join(value.split()).lower(),
    context=(
        "adres",
        "adresi",
        "address",
        "teslimat",
        "ship to",
        "lieferadresse",
        "adresse",
        "dirección",
        "indirizzo",
        "adres",
    ),
    context_boost=0.15,
    priority=2,
)
