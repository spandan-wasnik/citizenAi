"""Rule-based extraction of profile answers from free text (English, Hindi, Marathi, mixed).

Principles
  * Never guess. If the text is unclear, nothing is extracted; unclear amounts become a clarification.
  * Every value carries its provenance and the exact words it came from:
      explicit = the person said it about themselves;
      inferred = we derived it (monthly income x 12, a well-known city -> state, "student" -> enrolled).
    The conversation layer must confirm inferred values before they are used for eligibility.
  * Two different values for one field in one message are a conflict: nothing is extracted for it.
  * When the question just asked is known (`expected_field`), a bare answer ("30", "Maharashtra", "no") is
    accepted as an explicit answer to that question.
Lexicons are deliberately small and conservative; they are meant to be extended from the gold set in eval/.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.fields import STATE_NAMES
from app.nlp.normalize import END, START, Quantity, find_quantities, normalize_text


def N(s: str) -> str:
    return normalize_text(s)


def alt(*words: str) -> str:
    """Regex alternation of normalised words, longest first, with word edges."""
    ws = sorted({re.escape(N(w)) for w in words}, key=len, reverse=True)
    return f"{START}(?:{'|'.join(ws)}){END}"


def alt_prefix(*words: str) -> str:
    """Like alt, but the word may carry inflection after it (Marathi/Hindi endings): no right edge."""
    ws = sorted({re.escape(N(w)) for w in words}, key=len, reverse=True)
    return f"{START}(?:{'|'.join(ws)})"


@dataclass(frozen=True)
class Extraction:
    field: str
    value: Any
    provenance: str          # "explicit" | "inferred"
    evidence: str            # the words (normalised) the value came from
    note: str | None = None  # why it is only inferred


@dataclass(frozen=True)
class Clarification:
    field: str
    reason: str              # machine-readable, e.g. "income_whose"
    evidence: str
    value: int | None = None  # for income_whose: the amount, so the next answer ("family") can complete it


@dataclass
class ExtractionResult:
    extractions: list[Extraction] = field(default_factory=list)
    clarifications: list[Clarification] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)   # fields with two different values

    def get(self, name: str) -> Extraction | None:
        return next((e for e in self.extractions if e.field == name), None)

    def as_profile(self, *, include_inferred: bool = False) -> dict[str, Any]:
        return {e.field: e.value for e in self.extractions if include_inferred or e.provenance == "explicit"}


# ------------------------------------------------------------------------------------------ lexicons
I_WORD = alt("i", "i'm", "im", "मैं", "मै", "मी")
NEG = alt("no", "not", "never", "don't", "dont", "doesn't", "do not", "without", "neither", "nahi", "नहीं", "नही", "नाही", "नाहीत", "नसेल")
SPLIT_SEGMENTS = re.compile(r"[,;.!?।\n]+|" + alt("but", "and", "however", "though", "लेकिन", "परंतु", "मगर", "पण", "किंतु", "और", "आणि"))
SENTENCES = re.compile(r"[.!?।;\n]+")

STATE_ALIASES: dict[str, list[str]] = {
    "MH": ["महाराष्ट्र", "maharashtra"], "DL": ["दिल्ली", "delhi", "new delhi"], "UP": ["उत्तर प्रदेश", "uttar pradesh"],
    "MP": ["मध्य प्रदेश", "madhya pradesh"], "BR": ["बिहार", "bihar"], "GJ": ["गुजरात", "gujarat"],
    "RJ": ["राजस्थान", "rajasthan"], "KA": ["कर्नाटक", "कर्नाटका", "karnataka"],
    "TN": ["तमिलनाडु", "तमिळनाडू", "tamil nadu", "tamilnadu"], "KL": ["केरल", "केरळ", "kerala"],
    "PB": ["पंजाब", "punjab"], "HR": ["हरियाणा", "haryana"], "WB": ["पश्चिम बंगाल", "west bengal"],
    "OD": ["ओडिशा", "ओडिसा", "odisha", "orissa"], "TS": ["तेलंगाना", "तेलंगणा", "telangana"],
    "AP": ["आंध्र प्रदेश", "आंध्रप्रदेश", "andhra pradesh"], "CG": ["छत्तीसगढ", "chhattisgarh"],
    "JH": ["झारखंड", "jharkhand"], "UK": ["उत्तराखंड", "uttarakhand"], "HP": ["हिमाचल प्रदेश", "himachal pradesh"],
    "AS": ["असम", "आसाम", "assam"], "GA": ["गोवा", "goa"],
}
for _code, _name in STATE_NAMES.items():  # English names for every state/UT, so none is silently missing
    STATE_ALIASES.setdefault(_code, [])
    if _name.lower() not in [a.lower() for a in STATE_ALIASES[_code]]:
        STATE_ALIASES[_code].append(_name)
# Well-known, unambiguous cities only. A city gives an *inferred* state: it still has to be confirmed.
CITY_TO_STATE = {"pune": "MH", "पुणे": "MH", "mumbai": "MH", "मुंबई": "MH", "nagpur": "MH", "नागपूर": "MH",
                 "नागपुर": "MH", "nashik": "MH", "नाशिक": "MH", "thane": "MH", "ठाणे": "MH", "kolhapur": "MH",
                 "कोल्हापूर": "MH", "bengaluru": "KA", "bangalore": "KA", "chennai": "TN", "kolkata": "WB",
                 "hyderabad": "TS", "lucknow": "UP", "jaipur": "RJ", "patna": "BR", "bhopal": "MP"}

# Marathi/Hindi attach endings to place names (महाराष्ट्रात = in Maharashtra); allow a short, explicit list of them.
_ENDINGS = ("मध्ये", "मधील", "मधून", "ातील", "ाचा", "ाची", "ाचे", "ाला", "ात", "त", "ला", "चा", "ची", "चे")
_IRREGULAR_CITY_FORMS = {"पुणे": ["पुण्यात", "पुण्यातील", "पुण्याचा", "पुण्याची"], "ठाणे": ["ठाण्यात", "ठाण्याचा"],
                         "नागपूर": ["नागपुरात"], "नागपुर": ["नागपुर में"], "मुंबई": ["मुंबईत", "मुंबईचा", "मुंबईची"]}


def _place_regex(words) -> re.Pattern:
    parts = []
    for w in words:
        n = re.escape(N(w))
        if re.search("[\u0900-\u097F]", w):
            parts.append(f"{n}(?:{'|'.join(_ENDINGS)})?")
        else:
            parts.append(n)
    return re.compile(f"{START}(?:{'|'.join(sorted(parts, key=len, reverse=True))}){END}")


STATE_RE = {c: _place_regex(names) for c, names in STATE_ALIASES.items()}
CITY_FORMS = {**{c: c for c in CITY_TO_STATE}, **{f: c for c, fs in _IRREGULAR_CITY_FORMS.items() for f in fs}}
CITY_RE = _place_regex(list(CITY_TO_STATE) + list(CITY_FORMS))
LIVE = alt_prefix("live", "living", "lives", "stay", "staying", "reside", "residing", "resident", "रहता", "रहती", "रहते",
                  "रह रहा", "रह रही", "निवासी", "राहतो", "राहते", "राहत", "वास्तव्य", "रहवासी")
DOMICILE = alt_prefix("domicile", "domiciled", "मूल निवासी", "अधिवास", "अधिवासी", "डोमिसाइल")
FROM = alt("from", "से", "मधून", "येथील", "चा", "ची")  # "from X", "X से", "X मधून"

GENDER = {
    "female": alt("woman", "female", "girl", "lady", "महिला", "औरत", "लड़की", "लडकी", "स्त्री", "मुलगी", "बाई"),
    "male": alt("man", "male", "boy", "gentleman", "पुरुष", "आदमी", "लड़का", "लडका", "मुलगा"),
}
GAP_WORDS = {N(w) for w in ("a", "an", "am", "is", "also", "young", "old", "year", "years", "yr", "yrs", "year-old", "married",
                            "unmarried", "single", "एक", "साल", "की", "का", "ची", "चा", "आहे", "हूं", "है", "विवाहित", "अविवाहित")}
MAX_GAP_WORDS = 8
MAX_TEXT = 2000
SOCIAL = {
    "sc": [alt("sc", "scheduled caste", "scheduled castes"), alt("अनुसूचित जाति", "अनुसूचित जाती", "अनुसूचित जातीचा", "अनुसूचित जातीची")],
    "st": [alt("st", "scheduled tribe", "scheduled tribes"), alt("अनुसूचित जनजाति", "अनुसूचित जमाती", "अनुसूचित जमातीचा")],
    "obc": [alt("obc", "other backward class", "other backward classes"), alt("अन्य पिछड़ा वर्ग", "इतर मागासवर्ग", "इतर मागास प्रवर्ग", "इतर मागासवर्गीय")],
    "ews": [alt("ews", "economically weaker section", "economically weaker sections"), alt("आर्थिक दुर्बल", "आर्थिक रूप से कमजोर")],
    "general": [alt("general category", "open category", "general caste", "unreserved"), alt("सामान्य वर्ग", "सामान्य श्रेणी", "खुला प्रवर्ग", "खुल्या प्रवर्गातील")],
}
SOCIAL_CUE = re.compile(alt("category", "caste", "tribe", "belong", "belongs", "i am", "i'm", "वर्ग", "प्रवर्ग", "श्रेणी", "जाति", "जाती", "जमाती",
                            "मैं", "मै", "मी", "आहे", "हूं", "है", "से हूं", "चा", "ची"))
BARE_GENERAL = alt("general", "open", "gen", "unreserved", "सामान्य", "खुला", "खुल्या", "खुला वर्ग")
OCCUPATION = {
    "farmer": alt("farmer", "farming", "किसान", "शेतकरी", "खेती करता", "शेती करतो", "शेती करते"),
    "student": alt("student", "students", "विद्यार्थी", "विद्यार्थिनी", "छात्र", "छात्रा"),
    "unemployed": alt("unemployed", "jobless", "बेरोजगार", "बेकार"),
    "homemaker": alt("housewife", "homemaker", "गृहिणी", "गृहणी"),
    "retired": alt("retired", "सेवानिवृत्त", "निवृत्त", "रिटायर्ड"),
    "laborer": alt("laborer", "labourer", "labor", "मजदूर", "मजूर", "कामगार"),
    "self_employed": alt("self-employed", "self employed", "स्वयंरोजगार", "स्वयंरोजगारी"),
}
STUDYING = alt_prefix("studying", "pursuing", "enrolled", "पढ रहा", "पढ रही", "पढाई कर", "शिकत", "शिकतो", "शिकते", "अभ्यास करत")
LAND_WORD = alt_prefix("land", "farmland", "agricultural land", "acre", "acres", "hectare", "hectares", "जमीन", "जमिन", "खेत",
                       "एकड", "एकर", "शेती", "शेतजमीन", "शेत")
HAVE = alt_prefix("own", "owns", "have", "has", "मेरे पास", "माझ्याकडे", "मालक", "मालकी", "के मालिक", "होल्ड")
BANK = alt_prefix("bank account", "savings account", "bank a/c", "बैंक खाता", "बैंक खाते", "बँक खाते", "बँक खातं", "बँकेत खाते", "खाता", "खाते")
TAX = alt_prefix("income tax", "itr", "taxpayer", "tax payer", "आयकर", "इनकम टैक्स", "इन्कम टॅक्स")
TAX_PAY = alt_prefix("pay", "paying", "payer", "file", "filing", "भरता", "भरती", "भरते", "भरतो", "देता", "देती", "देतो", "देते", "देत")
PAST = alt("was", "earlier", "previously", "past", "formerly", "used to", "last year", "पहले", "पूर्वी", "आधी", "गेल्या वर्षी", "पिछले साल")
RURAL = alt_prefix("village", "rural", "गाव", "गांव", "ग्रामीण", "देहात")
URBAN = alt_prefix("city", "urban", "town", "शहर", "शहरी", "नगर")
INCOME = alt_prefix("income", "earning", "earnings", "salary", "आय", "आमदनी", "कमाई", "सैलरी", "वेतन", "पगार", "उत्पन्न", "उत्पन्न", "आमदानी")
FAMILY = alt_prefix("family", "household", "parents", "परिवार", "परिवार", "कुटुंब", "कुटुंबाचे", "घर", "पारिवारिक", "कौटुंबिक")
OWN_INCOME = alt("my own", "my", "personal", "मेरी", "मेरा", "मेरे", "माझे", "माझा", "माझी", "व्यक्तिगत", "वैयक्तिक")
MONTHLY = alt_prefix("per month", "monthly", "a month", "/month", "pm", "p.m.", "महीना", "महीने", "महिना", "महिन्याला", "मासिक", "प्रति माह", "दरमहा", "mahina")
YEARLY = alt_prefix("per year", "annual", "annually", "yearly", "a year", "per annum", "p.a.", "सालाना", "वार्षिक", "वर्षाला", "प्रतिवर्ष", "दरवर्षी")
YES = {N(w) for w in ("yes", "yeah", "yep", "y", "haan", "han", "ha", "हाँ", "हां", "हा", "हो", "होय", "जी हाँ", "जी हां", "बिल्कुल", "correct", "right")}
NO = {N(w) for w in ("no", "nope", "n", "nahi", "nahin", "नहीं", "नही", "नाही", "नको", "नाहीये")}

_BOOL_FIELDS = {"owns_agricultural_land", "has_bank_account", "is_enrolled_in_education", "receives_other_scholarship",
                "is_or_was_income_tax_payer", "disability_status"}


def _segments(norm: str) -> list[str]:
    return [s.strip() for s in SPLIT_SEGMENTS.split(norm) if s and s.strip()]


def _is_neg(seg: str) -> bool:
    return bool(re.search(NEG, seg))


# ------------------------------------------------------------------------------------------ extractors
def _age(norm: str, expected: str | None) -> list[Extraction]:
    pats = [
        rf"{START}(?:i am|i'm|im|aged?|age is|my age is|age)\s*[:\-]?\s*(?P<n>\d{{1,3}})\b(?!\s*(?:lakh|lac|crore|k\b|thousand|%|rs|rupee))",
        r"(?P<n>\d{1,3})\s*-?\s*(?:years?|yrs?|y/o)\s*-?\s*old",
        rf"{alt_prefix('उम्र', 'उमर', 'आयु', 'वय')}\D{{0,10}}(?P<n>\d{{1,3}})",
        rf"{I_WORD}\s*(?P<n>\d{{1,3}})\s*{alt_prefix('साल', 'वर्ष', 'बरस')}",
        rf"(?P<n>\d{{1,3}})\s*{alt_prefix('साल', 'वर्ष', 'बरस')}\S{{0,6}}\s*(?:की|का|के|ची|चा|चे)?\s*(?:आहे|आहेत|हूं|हूँ|है|हो|होगी)\b",
    ]
    found = []
    for p in pats:
        for m in re.finditer(p, norm):
            n = int(m.group("n"))
            if 0 < n <= 120:
                found.append(Extraction("age_years", n, "explicit", m.group(0)))
    if not found and expected == "age_years":
        m = re.fullmatch(r"\s*(?:i am\s*)?(\d{1,3})(?:\s*(?:years?|yrs?|साल|वर्ष\S*))?\s*", norm)
        if m and 0 < int(m.group(1)) <= 120:
            found.append(Extraction("age_years", int(m.group(1)), "explicit", m.group(0).strip()))
    return found


def _gender(norm: str) -> list[Extraction]:
    """'I am (a) [30 year old] woman': the words between the pronoun and the noun must all be harmless filler.
    Done word by word on purpose: a nested regex here can backtrack catastrophically on hostile input."""
    out = []
    i_re = re.compile(I_WORD)
    gender_re = {g: re.compile(w) for g, w in GENDER.items()}
    for sent in SENTENCES.split(norm):
        words = sent.split()
        for i, w in enumerate(words):
            if not i_re.fullmatch(w):
                continue
            for j in range(i + 1, min(len(words), i + 2 + MAX_GAP_WORDS)):
                tok = words[j]
                hit = next((g for g, rx in gender_re.items() if rx.fullmatch(tok)), None)
                if hit:
                    out.append(Extraction("gender", hit, "explicit", " ".join(words[i:j + 1])))
                    break
                if not (tok in GAP_WORDS or re.fullmatch(r"\d{1,3}", tok) or tok.startswith(N("वर्ष"))):
                    break
    return out


def _state(norm: str, expected: str | None) -> list[Extraction]:
    out: list[Extraction] = []
    for seg in _segments(norm):
        for code, rx in STATE_RE.items():
            m = rx.search(seg)
            if not m or _is_neg(seg):
                continue
            around = seg
            if re.search(DOMICILE, around):
                out.append(Extraction("domicile_state_code", code, "explicit", seg))
            elif re.search(LIVE, around):
                out.append(Extraction("state_code", code, "explicit", seg))
            elif re.search(FROM, around) and re.search(I_WORD, norm):
                out.append(Extraction("state_code", code, "inferred", seg, "said 'from', which may mean origin rather than residence"))
            elif expected in ("state_code", "domicile_state_code", "study_state_code") and re.fullmatch(rf"\s*(?:{rx.pattern})\s*", seg):
                out.append(Extraction(expected, code, "explicit", seg))
        cm = CITY_RE.search(seg)
        if cm and re.search(LIVE, seg) and not any(e.field == "state_code" for e in out):
            word = cm.group(0)
            base = CITY_FORMS.get(word) or next((c for c in CITY_TO_STATE if word.startswith(N(c))), None)
            if base:
                out.append(Extraction("state_code", CITY_TO_STATE[base], "inferred", seg, f"inferred from the city '{word}'"))
    return out


def _bool_answer(norm: str, expected: str | None) -> list[Extraction]:
    if expected in _BOOL_FIELDS:
        t = re.sub(r"[\s.!?।]+", " ", norm).strip()
        if t in YES:
            return [Extraction(expected, True, "explicit", t)]
        if t in NO:
            return [Extraction(expected, False, "explicit", t)]
    return []


def _bare_enum(norm: str, expected: str | None) -> list[Extraction]:
    """A bare 'female', 'SC', 'farmer' or 'village' answers the multiple-choice question that was just asked."""
    t = re.sub(r"[\s.!?।]+", " ", norm).strip()
    if not t or expected is None:
        return []
    if expected == "gender":
        for g, w in GENDER.items():
            if re.fullmatch(w, t):
                return [Extraction("gender", g, "explicit", t)]
    if expected == "social_category":
        for cat, rxs in SOCIAL.items():
            if any(re.fullmatch(rx, t) for rx in rxs):
                return [Extraction("social_category", cat, "explicit", t)]
        if re.fullmatch(BARE_GENERAL, t):  # only meaningful as the answer to the category question
            return [Extraction("social_category", "general", "explicit", t)]
    if expected == "occupation":
        for occ, rx in OCCUPATION.items():
            if re.fullmatch(rx, t):
                return [Extraction("occupation", occ, "explicit", t)]
    if expected == "residence_area":
        if re.fullmatch(RURAL, t):
            return [Extraction("residence_area", "rural", "explicit", t)]
        if re.fullmatch(URBAN, t):
            return [Extraction("residence_area", "urban", "explicit", t)]
    return []


def _land(norm: str) -> list[Extraction]:
    out = []
    for seg in _segments(norm):
        if not re.search(LAND_WORD, seg):
            continue
        if re.search(rf"{NEG}\s*(?:\w+\s+){{0,2}}{LAND_WORD}", seg) or (re.search(HAVE, seg) and _is_neg(seg)):
            out.append(Extraction("owns_agricultural_land", False, "explicit", seg))
        elif re.search(HAVE, seg):
            out.append(Extraction("owns_agricultural_land", True, "explicit", seg))
    return out


def _bank(norm: str) -> list[Extraction]:
    out = []
    for seg in _segments(norm):
        if not re.search(BANK, seg):
            continue
        has = re.search(alt_prefix("have", "has", "hold", "मेरे पास", "माझ्याकडे", "मेरा", "मेरी", "माझे", "माझं", "है", "आहे", "my"), seg)
        if _is_neg(seg):
            out.append(Extraction("has_bank_account", False, "explicit", seg))
        elif has:
            out.append(Extraction("has_bank_account", True, "explicit", seg))
    return out


def _tax(norm: str) -> list[Extraction]:
    out = []
    for seg in _segments(norm):
        if re.search(TAX, seg) and re.search(TAX_PAY, seg):
            if re.search(PAST, seg):
                continue  # "I paid last year" does not answer 'is or was a payer' safely
            out.append(Extraction("is_or_was_income_tax_payer", not _is_neg(seg), "explicit", seg))
    return out


def _education_and_occupation(norm: str) -> list[Extraction]:
    out = []
    for sent in SENTENCES.split(norm):
        if not re.search(I_WORD, sent):
            continue
        for seg in _segments(sent):
            for occ, rx in OCCUPATION.items():
                m = re.search(rx, seg)
                if m and not _is_neg(seg) and (re.search(I_WORD, seg) or seg is sent):
                    out.append(Extraction("occupation", occ, "explicit", seg))
                    if occ == "student":
                        out.append(Extraction("is_enrolled_in_education", True, "inferred", seg, "said 'student'"))
            if re.search(STUDYING, seg) and not _is_neg(seg):
                out.append(Extraction("is_enrolled_in_education", True, "explicit", seg))
            elif re.search(STUDYING, seg):
                out.append(Extraction("is_enrolled_in_education", False, "explicit", seg))
    return out


def _social(norm: str) -> list[Extraction]:
    out = []
    for seg in _segments(norm):
        for cat, rxs in SOCIAL.items():
            for rx in rxs:
                m = re.search(rx, seg)
                if m and re.search(SOCIAL_CUE, seg):
                    if _is_neg(seg):
                        continue
                    out.append(Extraction("social_category", cat, "explicit", seg))
    return out


def _area(norm: str) -> list[Extraction]:
    out = []
    for seg in _segments(norm):
        if re.search(LIVE, seg) and not _is_neg(seg):
            if re.search(RURAL, seg):
                out.append(Extraction("residence_area", "rural", "explicit", seg))
            elif re.search(URBAN, seg):
                out.append(Extraction("residence_area", "urban", "explicit", seg))
    return out


def _income(norm: str, quantities: list[Quantity], expected: str | None) -> tuple[list[Extraction], list[Clarification]]:
    ex: list[Extraction] = []
    cl: list[Clarification] = []
    money = [q for q in quantities if q.has_multiplier or q.currency_marked or q.value >= 1000]
    for m in re.finditer(INCOME, norm):
        near = [q for q in money if -40 <= q.start - m.end() <= 40 or -40 <= m.start() - q.end <= 40]
        if not near:
            continue
        q = min(near, key=lambda q: min(abs(q.start - m.end()), abs(m.start() - q.end)))
        lo, hi = max(0, min(m.start(), q.start) - 30), min(len(norm), max(m.end(), q.end) + 30)
        window = norm[lo:hi]
        evidence = norm[min(m.start(), q.start):max(m.end(), q.end)]
        amount = q.as_int
        if amount is None:
            continue
        monthly = bool(re.search(MONTHLY, norm[q.end:q.end + 25])) or bool(re.search(MONTHLY, norm[max(0, m.start() - 15):m.start()]))
        yearly = bool(re.search(YEARLY, window))
        if monthly and not yearly:
            amount, prov, note = amount * 12, "inferred", f"monthly amount x 12 (stated {q.raw})"
        else:
            prov, note = "explicit", None
        if re.search(FAMILY, window):
            ex.append(Extraction("annual_family_income", amount, prov, evidence, note))
        elif re.search(OWN_INCOME, window) or expected == "annual_individual_income":
            ex.append(Extraction("annual_individual_income", amount, prov, evidence, note))
        elif expected == "annual_family_income":
            ex.append(Extraction("annual_family_income", amount, prov, evidence, note))
        else:
            cl.append(Clarification("annual_family_income", "income_whose", evidence, amount))
    if not ex and not cl and expected in ("annual_family_income", "annual_individual_income") and len(money) == 1:
        q = money[0]
        if q.as_int is not None and re.fullmatch(r"[\s₹a-z.]*" + re.escape(q.raw) + r"[\s a-z.]{0,25}", norm):
            ex.append(Extraction(expected, q.as_int, "explicit", q.raw))
    return ex, cl


def extract_profile(text: str, expected_field: str | None = None) -> ExtractionResult:
    if len(text) > MAX_TEXT:
        raise ValueError(f"text longer than {MAX_TEXT} characters; refusing rather than silently cutting it")
    norm, quantities = find_quantities(text)
    res = ExtractionResult()
    found: list[Extraction] = []
    for part in (_age(norm, expected_field), _gender(norm), _state(norm, expected_field), _bool_answer(norm, expected_field),
                 _bare_enum(norm, expected_field),
                 _land(norm), _bank(norm), _tax(norm), _education_and_occupation(norm), _social(norm), _area(norm)):
        found += part
    inc, clar = _income(norm, quantities, expected_field)
    found += inc
    res.clarifications += clar
    # one value per field; two different values are a conflict, same value twice is fine (explicit beats inferred)
    by_field: dict[str, list[Extraction]] = {}
    for e in found:
        by_field.setdefault(e.field, []).append(e)
    for f, items in by_field.items():
        explicit = [e for e in items if e.provenance == "explicit"]
        pool = explicit or items
        if len({repr(e.value) for e in pool}) > 1:
            res.conflicts.append(f)
        else:
            res.extractions.append(pool[0])
    res.clarifications = [c for c in res.clarifications if not any(e.field in ("annual_family_income", "annual_individual_income") for e in res.extractions)]
    return res
