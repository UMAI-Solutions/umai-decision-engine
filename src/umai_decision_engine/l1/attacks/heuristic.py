"""Deterministic prompt-attack detector for Turkish and English (no model, no download).

Every text is looked at through several *views* that undo common obfuscation: Turkish case and
diacritic folding, zero-width characters, homoglyphs, leetspeak, letter spacing, split payloads
and base64. Weighted patterns over the views combine as a noisy-OR into one score. Quoted
attack phrases inside clearly educational text ("what is prompt injection, why is 'ignore
previous instructions' dangerous") are discounted.

It catches the common phrasings cheaply and explains itself; paraphrases need a model. Measured
on the `pi-tr` eval set (gateway WP4b).
"""

# ruff: noqa: E501  (a pattern table: one pattern per line reads better than wrapped regexes)

from __future__ import annotations

import base64
import binascii
import re
import unicodedata

# ------------------------------------------------------------------ views

_ZERO_WIDTH = re.compile("[​-‏‪-‮⁠-⁤﻿­]")
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i",
        "ј": "j", "ѕ": "s", "ԁ": "d", "ӏ": "l", "к": "k", "м": "m", "т": "t", "н": "h",
        "А": "a", "Е": "e", "О": "o", "Р": "p", "С": "c", "Х": "x", "К": "k", "М": "m",
        "Т": "t", "Н": "h", "В": "b", "α": "a", "ε": "e", "ο": "o", "ρ": "p", "τ": "t",
        "ν": "v", "κ": "k", "ι": "i", "Ο": "o", "Α": "a", "Ε": "e",
    }
)  # fmt: skip
_TURKISH = str.maketrans("çğıöşüâîûÇĞÖŞÜÂÎÛ", "cgiosuaiuCGOSUAIU")
_LEET = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
)
_QUOTES = re.compile(r"[’‘`´]")
_SPLIT_JOIN = re.compile(r"['\"“”]\s*\+\s*['\"“”]")
_BASE64 = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
_QUOTED = re.compile(r"'[^']{3,200}'|\"[^\"]{3,200}\"|“[^”]{3,200}”|‘[^’]{3,200}’")


def fold(text: str) -> str:
    """Lower-case ASCII view: Turkish folding, no zero-width, no homoglyphs, no apostrophes."""

    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH.sub("", text).translate(_HOMOGLYPHS)
    text = text.replace("İ", "i").replace("I", "i").translate(_TURKISH).lower()
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = _QUOTES.sub("", text).replace("'", "")
    return re.sub(r"\s+", " ", text).strip()


def _despaced(raw: str) -> str | None:
    """``"y o k   s a y"`` → ``"yok say"``: letters split by single spaces, words by runs."""

    tokens = [t for t in raw.split(" ") if t]
    singles = sum(1 for t in tokens if len(t) == 1)
    if len(tokens) < 8 or singles / len(tokens) < 0.5:
        return None
    marked = re.sub(r" {2,}", "\x00", raw).replace(" ", "").replace("\x00", " ")
    return fold(marked)


def _decoded_base64(text: str) -> list[str]:
    found = []
    for token in _BASE64.findall(text):
        try:
            decoded = base64.b64decode(token + "=" * (-len(token) % 4), validate=True)
            value = decoded.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if value and sum(c.isprintable() or c.isspace() for c in value) / len(value) > 0.9:
            found.append(value)
    return found


def views(text: str) -> list[str]:
    raw = _SPLIT_JOIN.sub("", text)
    base = fold(raw)
    out = [base, base.translate(_LEET)]
    spaced = _despaced(_ZERO_WIDTH.sub("", raw))
    if spaced:
        out.append(spaced)
    out.extend(fold(decoded) for decoded in _decoded_base64(raw))
    return out


# ------------------------------------------------------------------ patterns (on folded text)

_W = r"(?:\w+\s+)"
_PREV = (
    r"(?:onceki|yukaridaki|onceden|eski|ilk|baslangictaki|verilen|tum|butun|bu konusmadaki|"
    r"previous|prior|above|earlier|preceding|initial|all|your|the|any)"
)
_STRONG_OBJ = r"(?:talimat|komut|yonerge|direktif|instruction|prompt|system message)"
_WEAK_OBJ = r"(?:kural|kisitlama|rule|guideline|restriction)"
_IGNORE_TR = (
    r"(?:yok\s*say|unut|gormezden gel|dikkate alma|iptal et|gecersiz (?:say|kil)|"
    r"bir kenara birak|takma|devre disi birak|bypass|forget)"
)
_IGNORE_EN = r"(?:ignore|disregard|forget|override|bypass|skip)"
_EXTRACT_OBJ = (
    r"(?:sistem prompt|system prompt|gizli talimat|baslangic talimat|ilk mesaj|"
    r"initial instruction|hidden (?:system )?prompt|hidden instruction|yapilandirma metni|"
    r"configuration text|sana (?:basta |ilk )?verilen (?:\w+ )?(?:talimat|yonerge|metin|kural))"
)
_EXTRACT_VERB = (
    r"(?:yazdir|goster|tekrar et|kopyala|paylas|dok|print|reveal|show|repeat|output|display|"
    r"tell me)"
)
_SECRET = (
    r"(?:api anahtar|sifre|parola|password|api key|credential|secret|session token|"
    r"oturum bilgi|erisim anahtar|access key|private key)"
)
_DATA_NOUN = (
    r"(?:api anahtar|sifre|parola|kisisel veri|musteri kayit|musteri liste|kimlik bilgi|"
    r"credential|password|api key|personal data|customer record|e-?posta adres|email address|"
    r"oturum|session token|belge|dosya|document|e-?posta|email|inbox|gelen kutusu|mesaj)"
)

PATTERNS: list[tuple[str, re.Pattern[str], float]] = [
    (name, re.compile(rx), w)
    for name, rx, w in [
        # instruction override
        ("override_tr", rf"{_PREV}\s+{_W}{{0,2}}{_STRONG_OBJ}\w*\s+{_W}{{0,3}}{_IGNORE_TR}", 0.9),
        ("override_tr_weak", rf"{_PREV}\s+{_W}{{0,2}}{_WEAK_OBJ}\w*\s+{_W}{{0,3}}{_IGNORE_TR}", 0.5),
        ("override_en", rf"{_IGNORE_EN}\s+{_W}{{0,3}}{_STRONG_OBJ}", 0.85),
        ("override_en_weak", rf"{_IGNORE_EN}\s+{_W}{{0,3}}{_WEAK_OBJ}", 0.5),
        ("system_msg", r"sistem (?:mesaj|prompt|talimat)\w*\s+(?:\w+\s+){0,3}(?:dikkate alma|yok\s*say|unut|gormezden gel|forget|ignore)", 0.85),
        ("rules_changed", r"(?:talimat|kural|yonerge)\w*\s+(?:guncellendi|degisti|degistirildi|kaldirildi|devre disi|gecersiz)", 0.6),
        ("safety_off", r"(?:guvenlik|safety)\s+(?:kural|filtre|filter|rule)\w*\s+(?:\w+\s+){0,2}(?:kapat|devre disi|kaldir|disable|turn off|off)", 0.7),
        ("only_me", r"(?:bundan sonra|artik|from now on).{0,60}(?:sadece|yalnizca|only|tek patronun).{0,30}\b(?:ben|beni|benim|me|my)\b", 0.6),
        ("new_task", r"(?:yeni|new)\s+(?:gorev|talimat|komut|task|instruction)\w*\s*[:\-]", 0.35),
        ("dev_says", r"(?:gelistiriciden|from the developer)\s+yeni\s+talimat", 0.5),
        ("never_heard", r"(?:hic duymamis|never existed|hicbir hukmu yok|no longer applies|rafa kaldir|sifirdan basliyoruz|bos ver)", 0.45),
        ("operators", r"(?:seni kim ne icin ayarladiysa|programcilarin sana yazdigi|seni kisitlayan|setup text you were given|your operators told you)", 0.55),
        # unrestricted personas
        ("no_rules", r"(?:kural|kisitlama|filtre|sinir|sansur|politika)\w*\s+(?:\w+\s+){0,2}(?:olmayan|olmadig\w*|yok\b|yoktur|kaldirildi|devre disi|gecerli degil|bagli degil)", 0.6),
        ("no_rules_en", r"(?:no|without|zero)\s+(?:rules|restrictions|filters|limits|censorship)", 0.55),
        ("no_policy", r"(?:hicbir|hic bir)\s+(?:kural|politika|kisitlama|sinir)\w*", 0.45),
        ("anything_goes", r"(?:her seye izin|never refuses|always complies)", 0.45),
        ("uncensored", r"\b(?:sansursuz|kisitlamasiz|sinirsiz|filtresiz|unrestricted|uncensored|unfiltered)\b", 0.45),
        ("dan", r"\bdan(?: olarak|sin)\b|\bas dan\b|you are (?:now )?dan\b", 0.6),
        ("special_mode", r"\b(?:gelistirici|developer|god|jailbreak|root)\s+mod", 0.3),
        ("free_ai", r"(?:ozgur|free|unrestricted)\s+(?:asistan|assistant|ai|yapay zeka)", 0.45),
        ("grandma", r"(?:buyukanne|anneanne|babaanne|grandma|grandmother)\w*.{0,80}(?:sifre|parola|password|anahtar|key)", 0.65),
        ("fiction_villain", r"(?:senaryo|hikaye|film|story|screenplay|oyun|game)\w*.{0,80}(?:hacker|kotu karakter|villain|criminal|suclu)", 0.3),
        # prompt extraction
        ("extract_tr", rf"{_EXTRACT_OBJ}\w*\s+(?:\w+\s+){{0,5}}{_EXTRACT_VERB}", 0.8),
        ("extract_rev", rf"{_EXTRACT_VERB}\w*\s+(?:\w+\s+){{0,4}}{_EXTRACT_OBJ}", 0.75),
        ("initial_q", r"(?:what|which) (?:were|are) your (?:initial |original |hidden )?(?:instructions|rules|prompt)", 0.75),
        ("repeat_above", r"(?:repeat|copy|print) (?:the |all )?(?:text|words|everything|content) (?:above|before)", 0.75),
        ("repeat_tr", r"(?:yukaridaki|ilk mesajindaki|onceki) (?:metni|mesaji|talimatlari)\s+(?:\w+\s+){0,6}(?:tekrar|kopyala|yaz)", 0.75),
        ("which_rules", r"hangi (?:kural|talimat)\w*\s+(?:\w+\s+){0,2}calis\w*", 0.4),
        ("verbatim", r"(?:hicbir seyi atlamadan|eksiksiz|kelimesi kelimesine|verbatim|word for word|olduğu gibi paylas|oldugu gibi paylas)", 0.35),
        # exfiltration
        ("md_exfil", r"!\[[^\]]*\]\(https?://[^)\s]*\?[^)\s]*=", 0.65),
        ("all_data", rf"(?:tum|butun|all|every|elindeki)\s+(?:\w+\s+){{0,3}}{_DATA_NOUN}\w*", 0.4),
        ("secrets_out", rf"{_SECRET}\w*\s+(?:\w+\s+){{0,4}}(?:listele|goster|gonder|ver|paylas|list|show|send|share|reveal)", 0.5),
        ("secrets_out_en", rf"(?:listele|list|show|reveal|print|goster|dok|dump)\s+(?:\w+\s+){{0,3}}{_SECRET}", 0.5),
        ("data_to_me", rf"{_DATA_NOUN}\w*\s+(?:\w+\s+){{0,2}}(?:bana|me)\s+(?:gonder|ver|send|give)", 0.4),
        ("send_me_secret", rf"(?:send|give|gonder|ver)\s+(?:me|bana)\s+(?:\w+\s+){{0,3}}{_SECRET}", 0.45),
        ("contents_of_all", r"(?:tum|butun|all|every|elindeki)\s+(?:\w+\s+){0,2}(?:belge|dosya|document|file|kayit|record)\w*\s+(?:icerig|content)", 0.35),
        ("dump_whole", r"(?:dump|dok|export|disari aktar)\w*\s+(?:\w+\s+){0,2}(?:whole|entire|all|tum|butun|tamam)\w*\s+(?:\w+\s+){0,2}(?:table|database|veritaban|tablo|liste|kayit)", 0.55),
        ("to_address", r"(?:adresine|to)\s+(?:\w+\s+){0,1}(?:gonder|ilet|forward|send)", 0.3),
        ("dump_all", r"(?:tamamini|tumunu|whole|entire)\s+(?:\w+\s+){0,3}(?:dok|dump|cikar|export|disari aktar)", 0.55),
        ("dump", r"\b(?:dump|dok)\b", 0.25),
        ("other_users", r"(?:onceki|diger|other|previous) (?:kullanici|user|musteri)\w*\s+(?:\w+\s+){0,5}(?:goster|show|listele|list)", 0.6),
        ("url_params", r"(?:query param|url'?ye ekle|bu url|this url)", 0.35),
        # tool hijack
        ("no_consent_tr", r"(?:onay|izin)\w*\s+(?:sormadan|almadan|istemeden|beklemeden)", 0.55),
        ("no_consent_en", r"(?:without|no)\s+(?:asking|confirmation|approval|permission)", 0.55),
        ("behind_user", r"(?:kullaniciya sormadan|kullanicinin haberi olmadan|without telling the user)", 0.6),
        ("tool_call", r"\b[a-z]+_[a-z_]+\b\s*(?:arac\w*|tool)?\s*(?:\w+\s+){0,3}(?:calistir|cagir|uygula|run|call|invoke|execute)", 0.3),
        ("call_tool_en", r"(?:call|invoke|run|execute)\s+(?:the\s+)?[a-z]+_[a-z_]+", 0.35),
        # indirect injection markers
        ("note_to_ai", r"(?:yapay zeka|\bai\b|asistan|assistant|\bmodel\b|\bllm\b|\bbot\b|agent)\w*\s+(?:icin\s+)?(?:gizli\s+)?(?:not|talimat|instruction|note)\w*\s*[:)\-]", 0.55),
        ("not_paren", r"\bnot\s*\((?:yapay zeka|\bai\b|asistan|model)", 0.55),
        ("system_tag", r"\[(?:system|sistem)\]", 0.45),
        ("note_to_ai_en", r"note to (?:ai|assistant|llm|agent|model)s?", 0.6),
        ("reader_model", r"bu (?:e-?postayi|belgeyi|sayfayi|metni|talebi|dokumani) (?:okuyan|ozetleyen|isleyen|isleyecek) (?:model|asistan|yapay zeka|ai)", 0.7),
        ("processing_ai", r"bu talebi isleyen (?:yapay zeka|model|asistan)", 0.6),
        ("when_ai_reads", r"(?:when you|if you are an ai|ai agents?) (?:read|summari[sz]e|process)", 0.5),
        ("assistant_colon", r"(?:ai assistant|asistan|assistant|\bai)\s*:\s*(?:when|bu|eger|if|ignore|onceki)", 0.5),
        ("when_reading_tr", r"(?:asistan|assistant)\w*,?\s+(?:bu\s+)?(?:metni|sayfayi|e-?postayi)?\s*(?:okudugunda|okuyunca)", 0.55),
        ("ask_secret", r"(?:sifre|parola|password|kredi karti|kart bilgi|card number|card)\w*\s+(?:\w+\s+){0,2}(?:sor|iste|ask|request)\b", 0.35),
        ("tell_user_go", r"kullaniciya\s+(?:\w+\s+){0,4}(?:tikla|git|gir|dogrula|guven)\w*\s+(?:\w+\s+){0,1}(?:soyle|de|iste)", 0.5),
        ("tell_user_en", r"tell the user to (?:log in|click|visit|enter|trust)", 0.55),
        ("append_to_answer", r"(?:ozetin|yanitin|cevabin|answer|summary)\w*\s+(?:sonuna|basina|end|beginning)\s+(?:\w+\s+){0,8}(?:ekle|append|add)", 0.45),
        ("html_comment", r"<!--.{0,300}(?:talimat|instruction|asistan|assistant|\bai\b|yapay zeka)", 0.45),
        ("mark_candidate", r"(?:goz ardi et|gozardi et).{0,80}(?:isaretle|mark)", 0.45),
        # obfuscation and wrappers
        ("b64_exec", r"(?:base64|b64)\w*\s+(?:\w+\s+){0,3}(?:coz|decode)\w*\s+(?:\w+\s+){0,4}(?:uygula|calistir|execute|follow|yap)", 0.6),
        ("split_exec", r"(?:parcayi|parcalari|parts|pieces)\s+(?:\w+\s+){0,2}(?:birlestir|combine|concatenate)\w*\s+(?:\w+\s+){0,3}(?:uygula|calistir|execute|follow)", 0.55),
        ("granted", r"(?:erisim verildi|access granted)", 0.3),
        ("every_answer", r"(?:her|every) (?:yaniti|cevabi|answer|response)\w*\s+(?:\w+\s+){0,4}(?:bitir|end|basla)", 0.4),
    ]
]  # fmt: skip

_EDUCATIONAL = re.compile(
    r"(?:nedir|neden tehlikeli|nasil korun|nasil tani|ornek|egitim|seminer|rehber|"
    r"what is|why (?:is|are)|how (?:do|to) (?:we )?(?:prevent|defend|protect|recogni[sz]e)|"
    r"red team|kirmizi takim|tatbikat)"
)


def _noisy_or(weights: list[float]) -> float:
    remaining = 1.0
    for weight in weights:
        remaining *= 1.0 - weight
    return 1.0 - remaining


def explain(text: str) -> tuple[float, list[str]]:
    """The score and the names of the patterns that produced it."""

    candidates = views(text)
    if _EDUCATIONAL.search(candidates[0]):
        # Quoted attack phrases in teaching text are examples, not instructions.
        candidates = views(_QUOTED.sub(" ", text))
    best: tuple[float, list[str]] = (0.0, [])
    for view in candidates:
        hits = [(name, w) for name, rx, w in PATTERNS if rx.search(view)]
        score = _noisy_or([w for _, w in hits])
        if score > best[0]:
            best = (score, [name for name, _ in hits])
    return round(best[0], 4), best[1]


class HeuristicDetector:
    name = "heuristic"

    def scores(self, texts: list[str]) -> list[float]:
        return [explain(t)[0] for t in texts]
