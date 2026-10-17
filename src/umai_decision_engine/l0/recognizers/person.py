"""Person names (`PERSON`): given-name gazetteer + capitalised surname.

A deterministic, model-free baseline for Lakera's name detector (docs/reference/lakera-guard.md):
a known given name (``l0/data/first_names.txt``: Türkiye, US/UK, Europe), optionally a second
given name, then a capitalised surname (with particles such as ``de``, ``van``, ``O'``).
Single names, placeholders ("John Doe") and honorifics ("Mehmet Bey") are not names. Turkish
suffixes after the surname are excluded from the span (``Elif Koç'a`` → ``Elif Koç``). Names
outside the gazetteer need the NER model (open question Q8).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import regex

from umai_decision_engine.l0.recognizers.base import Candidate, PatternSpec, RecognizerSpec

_DATA = Path(__file__).resolve().parents[1] / "data"

# Capitalised words that follow a given name but are not surnames.
_NOT_SURNAMES = frozenset(
    {
        "bey",
        "beyefendi",
        "hanım",
        "hanımefendi",
        "hoca",
        "hocam",
        "efendi",
        "abla",
        "abi",
        "ağabey",
        "amca",
        "teyze",
        "usta",
        "dayı",
        "enişte",
        "yenge",
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sir",
        "madam",
        "inc",
        "ltd",
        "llc",
        "gmbh",
        "bank",
        "group",
        "holding",
        "street",
        "avenue",
        "road",
        "lane",
        "caddesi",
        "sokak",
        "mahallesi",
        "bulvarı",
        "airport",
        "university",
        "üniversitesi",
        "hastanesi",
        "hospital",
        "the",
        "and",
        "ve",
    }
)
_PARTICLES = r"(?:(?:de|van|von|der|den|da|di|del|della|du|dos|das|la|le|ter|ten)\s)"


@lru_cache(maxsize=1)
def _given_names() -> tuple[str, ...]:
    names = {
        line.strip()
        for line in (_DATA / "first_names.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    return tuple(sorted(names, key=len, reverse=True))  # longest first for the alternation


def _pattern() -> str:
    given = "|".join(regex.escape(name) for name in _given_names())
    surname = r"(?:O['’])?\p{Lu}\p{Ll}+(?:-\p{Lu}\p{Ll}+)?"
    return (
        rf"(?<![\p{{L}}])(?:{given})(?:\s(?:{given}))?\s{_PARTICLES}{{0,2}}{surname}(?![\p{{L}}])"
    )


def _refine_person(fragment: str) -> list[Candidate]:
    words = fragment.split()
    if len(words) < 2:
        return []
    last = words[-1].replace("’", "'").split("'")[-1].split("-")[0].lower()
    if last in _NOT_SURNAMES:
        return []
    return [Candidate(0, len(fragment), None)]


def _canonical(value: str) -> str:
    return " ".join(value.split()).lower()


PERSON = RecognizerSpec(
    entity="PERSON",
    patterns=(PatternSpec("given_name_surname", _pattern(), 0.6),),
    canonical=_canonical,
    refine=_refine_person,
    context=(
        "sayın",
        "adı",
        "ad soyad",
        "isim",
        "müşteri",
        "hasta",
        "mr",
        "mrs",
        "ms",
        "dr",
        "dear",
        "name",
        "signed",
        "monsieur",
        "madame",
        "herr",
        "frau",
        "signor",
        "señor",
    ),
    context_boost=0.2,
    known_non_pii=frozenset({"john doe", "jane doe", "max mustermann", "erika mustermann"}),
    priority=1,
)
