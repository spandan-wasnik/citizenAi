"""Small parsers for short replies to a question: yes/no, 'skip', and 'whose income'. English, Hindi, Marathi."""
from __future__ import annotations

import re

from app.nlp.extract import FAMILY, NO, YES, alt
from app.nlp.intent import phrase
from app.nlp.normalize import normalize_text

SKIP = phrase("skip", "pass", "prefer not", "rather not", "don't want to say", "dont want to say", "don't know", "dont know",
              "not sure", "no idea", "पता नहीं", "नहीं बताना", "बताना नहीं", "नहीं बताऊंगा", "छोड़ो", "माहित नाही", "माहीत नाही",
              "सांगायचे नाही", "सांगायचं नाही", "नको सांगू", "सोडा")
INDIVIDUAL = re.compile("|".join([alt("individual", "personal", "only me", "just me", "my own", "self", "my", "मेरी", "मेरा", "मेरे",
                                      "माझे", "माझा", "माझी", "स्वतः", "व्यक्तिगत", "वैयक्तिक", "सिर्फ मेरी", "फक्त माझे")]))


def parse_yes_no(text: str) -> bool | None:
    t = re.sub(r"[\s.!?।,]+", " ", normalize_text(text)).strip()
    if t in YES:
        return True
    if t in NO:
        return False
    return None


def parse_skip(text: str) -> bool:
    return bool(SKIP.search(normalize_text(text)))


def parse_whose(text: str) -> str | None:
    """'family' or 'individual' when the reply says whose income it is; None when unclear or both."""
    t = normalize_text(text)
    fam, ind = bool(re.search(FAMILY, t)), bool(INDIVIDUAL.search(t))
    if fam == ind:
        return None
    return "family" if fam else "individual"
