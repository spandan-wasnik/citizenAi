"""Language detection for English, Hindi and Marathi, including romanised (Latin-script) Hindi/Marathi.

Method (deterministic, no model download):
  1. Count letters by script. Devanagari vs Latin decides the script; both present = mixed.
  2. Devanagari text: Hindi and Marathi share a script, so decide by marker words (है/मैं vs आहे/मी ...).
  3. Latin text: compare English function words against romanised Hindi and Marathi marker words.
  4. If the evidence is missing or tied the answer is language=None ("unknown") and the caller keeps the
     conversation's current language or asks. We never silently pick a language on no evidence.

Marker lists are small and conservative; words that are common to two languages (e.g. 'nahi') count for neither.
The author-written gold set is in eval/language_gold.json; scripts/eval_language.py reports accuracy and a
confusion matrix on it, or on any file in the same format (add real messages from real users there).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.nlp.normalize import normalize_text

LANGS = ("en", "hi", "mr")


def _words(s: str) -> frozenset[str]:
    return frozenset(normalize_text(w) for w in s.split())


# Devanagari markers (normalised form: no nukta, anusvara). Words used by both languages are left out on purpose.
HI_DEV = _words("है हैं हूं था थी थे मैं मै मेरा मेरी मेरे हमारा हमारी हमारे और नहीं में को से क्या कैसे कौन कहां कहाँ कब मुझे मुझको मिलेगी मिलेगा लोग रहता रहती करता करती "
                "कर रहा रही चाहिए चाहता चाहती तुम आप आपका आपकी यह वह कितना कितनी मिलेगा मिलता")
MR_DEV = _words("आहे आहेत होते होती होतो मी माझे माझा माझी माझं माझ्या आम्ही आमचे आमचा आमची आणि नाही नाहीत मध्ये काय कसे कसा कशी कोणती कोणता कोणत्या लागतील लागेल करायचा करायची कुठे केव्हा "
                "राहतो राहते करतो करते करत पाहिजे हवे हवी तुम्ही तुमचे तुमचा तुमची किती मिळेल मिळते मला तुला")
# 'का' 'की' 'के' 'को' 'से' 'पर' 'तुम' are Hindi particles; 'चा' 'ची' 'चे' 'ला' are Marathi suffix particles. Single
# letters/short particles are weak evidence, so they are weighted lower below.
_WEAK = _words("को से")

# Romanised markers (lower-case). 'nahi', 'lakh', 'kya' style shared words are excluded or handled as shared.
HI_ROM = frozenset("main mera meri mere hum hamara hamari hai hain hoon hun ho tha thi aur kya kaise kitna kitni "
                   "mujhe mujhko apka apki aapka aapki yojana saal umar umr mahila aurat ladki paas jameen zameen "
                   "rehta rehti rahta rahti karta karti chahiye milega milta kisan khet parivar pariwar mein se ko ka ki ke".split())
MR_ROM = frozenset("mi majhe majha majhi majhya amhi amche amcha amchi ahe aahe ahet aahet ani kay kase kiti mala tula "
                   "pahije hava havi rahto rahte karto karte shetkari shet varsh vay kutumb kutumbache milel milto "
                   "tumhi tumche tumcha tumchi mahila hoy nako".split())
SHARED_ROM = frozenset("nahi nahin lakh rupaye rupees".split())  # appear in both; carry no evidence
_WEAK_ROM = frozenset("ho ko ka ki ke mein se main hum".split())  # 'main' is also an English word; 'ho' etc. are short
EN = frozenset("the is am are was were my i i'm have has had and of to in on for with from live lives living income "
               "family year years old age which what how can do does not no yes want need scheme schemes eligible eligibility "
               "apply application documents document land bank account student farmer woman man female male please "
               "tell about me you your where who when why it this that".split())
# English words that are also romanised-Hindi/Marathi words are removed from the English list for scoring.
_EN_AMBIGUOUS = frozenset("main ho me".split())

_TOKEN = re.compile(r"[A-Za-z']+|[ऀ-ॿ]+")


@dataclass(frozen=True)
class Detection:
    language: str | None                 # 'en' | 'hi' | 'mr' | None (not enough evidence)
    script: str                          # 'latin' | 'devanagari' | 'mixed' | 'none'
    devanagari_share: float
    evidence: dict[str, int] = field(default_factory=dict)   # score per language, for explainability
    reason: str = ""                     # short machine-readable reason, e.g. 'tie', 'no_markers', 'ok'

    @property
    def strong(self) -> bool:
        """Enough evidence to *switch* the conversation language (one stray word such as 'yes' is not)."""
        return self.language is not None and self.evidence.get(self.language, 0) >= 4

    @property
    def romanised(self) -> bool:
        return self.script == "latin" and self.language in ("hi", "mr")


def _script_counts(text: str) -> tuple[int, int]:
    dev = sum(1 for c in text if "ऀ" <= c <= "ॿ" and c.isalpha() or "ा" <= c <= "्")
    lat = sum(1 for c in text if c.isascii() and c.isalpha())
    return dev, lat


def detect_language(text: str) -> Detection:
    norm = normalize_text(text)
    dev, lat = _script_counts(norm)
    total = dev + lat
    if total == 0:
        return Detection(None, "none", 0.0, {}, "no_letters")
    share = dev / total
    script = "devanagari" if share >= 0.8 else "latin" if share <= 0.2 else "mixed"
    tokens = _TOKEN.findall(norm)
    dev_tokens = [t for t in tokens if "ऀ" <= t[0] <= "ॿ"]
    lat_tokens = [t for t in tokens if t[0].isascii()]

    score = {"en": 0, "hi": 0, "mr": 0}
    for t in dev_tokens:
        w = 1 if t in _WEAK else 2
        if t in HI_DEV and t not in MR_DEV:
            score["hi"] += w
        elif t in MR_DEV and t not in HI_DEV:
            score["mr"] += w
    for t in lat_tokens:
        if t in SHARED_ROM:
            continue
        w = 1 if t in _WEAK_ROM else 2
        in_hi, in_mr, in_en = t in HI_ROM, t in MR_ROM, (t in EN and t not in _EN_AMBIGUOUS)
        if in_hi and not in_mr and not in_en:
            score["hi"] += w
        elif in_mr and not in_hi and not in_en:
            score["mr"] += w
        elif in_en and not in_hi and not in_mr:
            score["en"] += 2

    if script == "devanagari":
        candidates = {k: v for k, v in score.items() if k != "en"}
    elif script == "latin":
        candidates = dict(score)
    else:  # mixed: the language is whichever Indic markers or English words dominate
        candidates = dict(score)
    best = max(candidates.values())
    if best == 0:
        return Detection(None, script, share, score, "no_markers")
    winners = [k for k, v in candidates.items() if v == best]
    if len(winners) > 1:
        return Detection(None, script, share, score, "tie")
    return Detection(winners[0], script, share, score, "ok")
