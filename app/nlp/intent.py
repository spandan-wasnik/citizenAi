"""Rule-based intent classification and scheme-mention detection for English, Hindi and Marathi.

Returns the primary intent, every intent that matched (a message can ask two things), the schemes it names and,
for a language-switch request, the target language. Unclear text is 'other': the conversation layer then decides
whether the message is an answer to the question just asked. A bare greeting counts only for short messages.
Aliases for schemes are checked against the real scheme files by a test, so an alias can never point at a
scheme that does not exist.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.nlp.extract import alt, alt_prefix
from app.nlp.normalize import normalize_text

DEV = re.compile(r"[ऀ-ॿ]")


def phrase(*words: str) -> re.Pattern:
    """Latin phrases need word edges; Devanagari phrases may carry endings, so they match as prefixes."""
    latin = [w for w in words if not DEV.search(w)]
    dev = [w for w in words if DEV.search(w)]
    parts = ([alt(*latin)] if latin else []) + ([alt_prefix(*dev)] if dev else [])
    return re.compile("|".join(parts))


# Order matters: earlier intents win as the primary one.
RULES: list[tuple[str, re.Pattern]] = [
    ("restart", phrase("start over", "start again", "restart", "reset", "begin again", "clear everything", "forget everything",
                       "new check", "फिर से शुरू", "नए सिरे से", "दोबारा शुरू", "रीसेट", "सब मिटा", "पुन्हा सुरू", "पुन्हा सुरुवात",
                       "नव्याने सुरू", "सगळे पुसून")),
    ("language_switch", phrase("in hindi", "in marathi", "in english", "speak hindi", "speak marathi", "speak english", "hindi please",
                               "marathi please", "english please", "switch to hindi", "switch to marathi", "switch to english",
                               "हिंदी में", "मराठी में", "अंग्रेजी में", "इंग्लिश में", "हिंदीत", "मराठीत", "इंग्रजीत", "इंग्रजी मध्ये",
                               "हिंदी मध्ये", "मराठी मध्ये")),
    ("correction", phrase("actually", "i meant", "i mean", "correction", "that's wrong", "that is wrong", "not correct", "change my",
                          "sorry", "दरअसल", "मेरा मतलब", "गलत", "सुधार", "बदल", "म्हणजे", "चुकीचे", "चुकीचं", "चूक")),
    ("documents", phrase("document", "documents", "papers", "paperwork", "what do i need to bring", "दस्तावेज", "कागजात", "कागज",
                         "डॉक्युमेंट", "कागदपत्र", "दस्तऐवज")),
    ("eligibility", phrase("eligible", "eligibility", "qualify", "can i get", "am i entitled", "can i apply", "पात्र", "पात्रता",
                           "हकदार", "मुझे मिल सकता", "मला मिळेल का", "मला मिळू शकते", "मिळू शकेल", "आवेदन कर सकता", "आवेदन कर सकती", "अर्ज करू शकतो", "अर्ज करू शकते")),
    ("how_to_apply", phrase("how to apply", "how do i apply", "how can i apply", "application process", "apply online", "procedure",
                            "steps to apply", "where to apply", "want to apply", "apply for", "आवेदन करना", "अर्ज करायचा आहे", "आवेदन कैसे", "आवेदन कहां", "आवेदन कहाँ", "कैसे करें आवेदन", "प्रक्रिया",
                            "अर्ज कसा", "अर्ज कुठे", "अर्ज करायचा", "अर्ज कसे")),
    ("official_link", phrase("official website", "official link", "official site", "portal", "website", "link", "url",
                             "आधिकारिक वेबसाइट", "वेबसाइट", "लिंक", "पोर्टल", "अधिकृत संकेतस्थळ", "संकेतस्थळ")),
    ("discover", phrase("which schemes", "what schemes", "schemes for", "list schemes", "show schemes", "available schemes", "any scheme",
                        "suggest schemes", "find schemes", "finding schemes", "find me schemes", "schemes can i", "schemes i can", "कौन सी योजना", "कौन-सी योजना", "कौन सी योजनाएं", "कौन सी योजनाएँ", "कोई योजना", "योजनाएं",
                        "योजनाएँ", "कोणती योजना", "कोणत्या योजना", "योजना सांगा", "योजना दाखवा")),
    ("scheme_info", phrase("what is", "tell me about", "explain", "details of", "about", "benefits", "क्या है", "के बारे में", "लाभ",
                           "जानकारी", "काय आहे", "बद्दल", "माहिती")),
    ("help", phrase("help", "what can you do", "how does this work", "मदद", "मदत")),
    ("thanks", phrase("thanks", "thank you", "धन्यवाद", "आभार")),
]
GREETING = phrase("hello", "hi", "hey", "namaste", "namaskar", "नमस्ते", "नमस्कार", "हॅलो", "हेलो")
LANGUAGE_TARGETS = [("hi", phrase("hindi", "हिंदी")), ("mr", phrase("marathi", "मराठी")), ("en", phrase("english", "अंग्रेजी", "इंग्लिश", "इंग्रजी"))]

SCHEME_ALIASES: dict[str, list[str]] = {
    "pm-kisan": ["pm kisan", "pm-kisan", "pmkisan", "kisan samman", "pradhan mantri kisan", "पीएम किसान", "पीएम-किसान", "किसान सम्मान"],
    "atal-pension-yojana": ["atal pension", "apy", "अटल पेंशन", "अटल पेन्शन"],
    "mukhyamantri-majhi-ladki-bahin": ["ladki bahin", "ladaki bahin", "majhi ladki bahin", "ladki bahin yojana", "लाडकी बहीण", "लाडकी बहिण", "लाडकी बहिन", "लाडकी बहीन", "लाडकी बहन"],
    "post-matric-scholarship-sc": ["post matric", "post-matric", "postmatric", "पोस्ट मॅट्रिक", "पोस्ट मैट्रिक"],
    "rajarshi-shahu-maharaj-ebc-fee-concession": ["shahu maharaj", "ebc fee", "ebc concession", "राजर्षी शाहू", "शाहू महाराज", "शिक्षण शुल्क"],
}
_SCHEME_RE = {sid: phrase(*als) for sid, als in SCHEME_ALIASES.items()}


@dataclass(frozen=True)
class Intent:
    primary: str                                   # one of the names in RULES, 'greeting' or 'other'
    all: tuple[str, ...] = ()                      # every intent that matched, in priority order
    scheme_ids: tuple[str, ...] = ()               # schemes the message names
    target_language: str | None = None             # only for language_switch
    evidence: dict[str, str] = field(default_factory=dict)   # matched words per intent


def find_scheme_mentions(text: str) -> list[str]:
    norm = normalize_text(text)
    return [sid for sid, rx in _SCHEME_RE.items() if rx.search(norm)]


def classify_intent(text: str) -> Intent:
    norm = normalize_text(text)
    hits: list[str] = []
    evidence: dict[str, str] = {}
    for name, rx in RULES:
        m = rx.search(norm)
        if m:
            hits.append(name)
            evidence[name] = m.group(0)
    if not hits and len(norm.split()) <= 4 and (m := GREETING.search(norm)):
        hits.append("greeting")
        evidence["greeting"] = m.group(0)
    target = None
    if "language_switch" in hits:
        target = next((code for code, rx in LANGUAGE_TARGETS if rx.search(norm)), None)
    schemes = tuple(find_scheme_mentions(text))
    # "tell me about PM Kisan": naming a scheme with an info word is scheme_info even when 'about' is all there is.
    return Intent(hits[0] if hits else "other", tuple(hits), schemes, target, evidence)
