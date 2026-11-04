"""Text normalisation and number/amount recognition for English, Hindi and Marathi.

Everything here is deterministic and rule based. Nothing is guessed: text that is not clearly a
number is not returned as one, and ambiguous input (for example the range "2-3 lakh") yields no
quantity rather than a made-up one.

The normalised text is a *matching copy*: Unicode NFC, Devanagari digits turned into ASCII
digits, zero-width characters removed, nukta dropped and chandrabindu turned into anusvara
(so हज़ार/हजार and पाँच/पांच match the same lexicon entry), Latin text lower-cased. Positions
returned by `find_quantities` refer to this normalised copy, which is also returned.

The Hindi/Marathi word lists below are small on purpose (only forms that are regular and
common in talking about money). They are the first thing to review with a native speaker.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

_DEVANAGARI_DIGITS = {ord(c): str(i) for i, c in enumerate("०१२३४५६७८९")}
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
_NUKTA = "़"
_CHANDRABINDU, _ANUSVARA = "ँ", "ं"

# --- lexicons (already in matching form: no nukta, anusvara instead of chandrabindu) -------------
MULTIPLIERS: dict[str, int] = {
    # lakh = 1,00,000
    "lakh": 10**5, "lakhs": 10**5, "lac": 10**5, "lacs": 10**5, "laakh": 10**5, "lakh.": 10**5,
    "लाख": 10**5,
    # crore = 1,00,00,000
    "crore": 10**7, "crores": 10**7, "cr": 10**7, "करोड": 10**7, "कोटी": 10**7,
    # thousand
    "thousand": 10**3, "k": 10**3, "hazar": 10**3, "hazaar": 10**3, "hajar": 10**3, "हजार": 10**3,
    "hundred": 10**2, "सौ": 10**2, "शे": 10**2, "शंभर": 10**2,
}
_WORD_NUMBERS: dict[str, Decimal] = {}
for _words, _val in {
    ("one", "एक"): 1, ("two", "दो", "दोन"): 2, ("three", "तीन"): 3, ("four", "चार"): 4,
    ("five", "पांच", "पाच"): 5, ("six", "छह", "छः", "छे", "सहा"): 6, ("seven", "सात"): 7,
    ("eight", "आठ"): 8, ("nine", "नौ", "नऊ"): 9, ("ten", "दस", "दहा"): 10,
    ("twenty", "बीस", "वीस"): 20, ("twenty-five", "पच्चीस", "पंचवीस"): 25, ("thirty", "तीस"): 30,
    ("forty", "चालीस", "चाळीस"): 40, ("fifty", "पचास", "पन्नास"): 50, ("sixty", "साठ"): 60,
    ("seventy", "सत्तर"): 70, ("seventy-five", "पचहत्तर", "पंचाहत्तर"): 75, ("eighty", "अस्सी"): 80,
    ("ninety", "नब्बे", "नव्वद"): 90,
}.items():
    for _w in _words:
        _WORD_NUMBERS[_w] = Decimal(_val)
# Fractional idioms used with lakh/crore/thousand: डेढ़ लाख = 1.5 lakh, ढाई लाख = 2.5 lakh, सवा लाख = 1.25 lakh.
_FRACTION_WORDS: dict[str, Decimal] = {
    "डेढ": Decimal("1.5"), "दीड": Decimal("1.5"), "ढाई": Decimal("2.5"), "अडीच": Decimal("2.5"),
    "सवा": Decimal("1.25"), "सव्वा": Decimal("1.25"),
}
_CURRENCY = ("₹", "rs.", "rs", "inr", "रु.", "रु", "रुपये", "रुपए", "रुपया", "rupees", "rupee")

# Hindi/Marathi words are made of letters AND combining marks, so \b is not reliable; use explicit edges.
_L = "A-Za-z0-9ऀ-ॿ"
_START, _END = f"(?<![{_L}])", f"(?![{_L}])"
START, END = _START, _END  # for other modules that build word-edge patterns

_GROUPED = r"\d{1,3}(?:,\d{2})*,\d{3}(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?"  # 2,50,000 and 250,000
_PLAIN = r"\d+(?:\.\d+)?"
_NUMBER_TOKEN = "|".join(sorted((re.escape(w) for w in {**_WORD_NUMBERS, **_FRACTION_WORDS}), key=len, reverse=True))
_MULT_TOKEN = "|".join(sorted((re.escape(w) for w in MULTIPLIERS), key=len, reverse=True))
_CUR_TOKEN = "|".join(sorted((re.escape(w) for w in _CURRENCY), key=len, reverse=True))

_QUANTITY = re.compile(
    rf"(?P<cur>{_CUR_TOKEN})?\s*"
    rf"(?P<num>(?<![{_L}.,])(?:{_GROUPED}|{_PLAIN})(?![\d,]*\d)|(?<![{_L}])(?:{_NUMBER_TOKEN})(?![{_L}]))"
    rf"(?:\s*(?P<mult>{_MULT_TOKEN})(?![{_L}]))?",
    re.IGNORECASE,
)
_RANGE_BEFORE = re.compile(r"[\dऀ-ॿ]\s*(?:-|–|—|to|से|ते)\s*$", re.IGNORECASE)
_RANGE_AFTER = re.compile(r"^\s*(?:-|–|—|to|से|ते)\s*[\d]", re.IGNORECASE)


def normalize_text(text: str) -> str:
    """The matching copy described in the module docstring. Idempotent."""
    t = unicodedata.normalize("NFC", text)
    t = t.translate(_ZERO_WIDTH).translate(_DEVANAGARI_DIGITS)
    t = unicodedata.normalize("NFD", t)
    # NFD splits nukta forms such as ड़ into ड + nukta; dropping the nukta merges the spelling variants
    t = t.replace(_NUKTA, "").replace(_CHANDRABINDU, _ANUSVARA)
    t = unicodedata.normalize("NFC", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t.lower()


@dataclass(frozen=True)
class Quantity:
    value: Decimal            # the number after applying lakh/crore/thousand
    start: int                # span in the normalised text
    end: int
    raw: str                  # the matched text, as it appears in the normalised copy
    has_multiplier: bool
    currency_marked: bool     # a rupee marker (₹, Rs, रु) was attached

    @property
    def as_int(self) -> int | None:
        """The value as a whole number, or None if it is not one (we never round money)."""
        return int(self.value) if self.value == self.value.to_integral_value() else None


def number_word_value(word: str) -> Decimal | None:
    """Value of a single number word in English, Hindi or Marathi (for contexts such as age), else None."""
    return _WORD_NUMBERS.get(normalize_text(word))


def find_quantities(text: str) -> tuple[str, list[Quantity]]:
    """Normalise, then find every number with its optional multiplier and rupee marker.

    Returns (normalised_text, quantities in order). Numbers that are part of a range
    ("2-3 lakh", "2 से 3 लाख") are skipped because choosing one end would be a guess.
    """
    norm = normalize_text(text)
    out: list[Quantity] = []
    for m in _QUANTITY.finditer(norm):
        num_s, mult_s = m.group("num"), m.group("mult")
        if _RANGE_BEFORE.search(norm[: m.start("num")]) or _RANGE_AFTER.match(norm[m.end("num"):]):
            continue
        if num_s in _FRACTION_WORDS:
            if not mult_s:  # "डेढ" alone is not a number we can use
                continue
            base = _FRACTION_WORDS[num_s]
        elif num_s in _WORD_NUMBERS:
            base = _WORD_NUMBERS[num_s]
        else:
            base = Decimal(num_s.replace(",", ""))
        if num_s in _WORD_NUMBERS and not mult_s and not m.group("cur"):
            continue  # a bare word like 'one' or 'ten' is ordinary prose, not an amount
        value = base * (MULTIPLIERS[mult_s.lower()] if mult_s else 1)
        out.append(Quantity(value, m.start("cur") if m.group("cur") else m.start("num"), m.end(),
                            norm[(m.start("cur") if m.group("cur") else m.start("num")):m.end()],
                            bool(mult_s), bool(m.group("cur"))))
    return norm, out
