"""Text helpers for Hindi/Marathi (Devanagari) search.

SQLite's default FTS5 tokenizer treats Devanagari vowel signs and other combining
marks as separators, which shreds words into fragments. Declaring those marks as
``tokenchars`` keeps whole words. Digits are normalised so that Devanagari and ASCII
numerals match each other. Use ``normalize_text`` on BOTH indexed text and queries.
"""
from __future__ import annotations

import unicodedata

_DEVANAGARI_DIGITS = {ord(c): str(i) for i, c in enumerate("०१२३४५६७८९")}

DEVANAGARI_MARKS = "".join(
    chr(cp)
    for cp in (
        *range(0x0900, 0x0904),   # candrabindu, anusvara, visarga
        *range(0x093A, 0x093D),   # nukta and related signs
        *range(0x093E, 0x0950),   # vowel signs, virama
        *range(0x0951, 0x0958),   # stress and tone marks
        0x0962, 0x0963,           # vocalic vowel signs
    )
)


def normalize_text(text: str) -> str:
    """NFC-normalise and convert Devanagari digits to ASCII digits."""
    return unicodedata.normalize("NFC", text).translate(_DEVANAGARI_DIGITS)


def fts5_tokenizer_spec() -> str:
    """Value for the ``tokenize=`` option of an FTS5 table (use inside double quotes)."""
    return f"unicode61 tokenchars '{DEVANAGARI_MARKS}'"
