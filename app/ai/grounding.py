"""Checks on model-written text before anyone sees it. A failed check means the extractive answer is shown instead.

The checks are deliberately blunt: they cannot prove an answer is right, but they catch the common ways a model goes
beyond its sources: invented numbers, invented citations, other schemes, links, verdicts, or the wrong language."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.nlp.intent import find_scheme_mentions
from app.nlp.normalize import normalize_text

CITATION = re.compile(r"\[(\d{1,2})\]")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
URLISH = re.compile(r"(?i)(https?://|www\.|\b[a-z0-9\-]+\.(?:gov|nic|org|com|in|co)(?:\.[a-z]{2})?\b/?)")
SENTENCE_END = re.compile(r"(?<!\bRs)(?<!\bNo)(?<!\bMr)(?<!\bSt)[.!?।](?=\s+\S)")
VERDICTS = [r"\byou (?:are|will be|would be) (?:definitely |certainly )?eligible\b", r"\byou (?:will|would) (?:definitely )?(?:get|receive)\b",
            r"\bguarantee", r"\byou do not qualify\b", r"आप (?:निश्चित रूप से )?पात्र हैं", r"आपको (?:निश्चित )?मिलेगा", r"तुम्ही पात्र आहात", r"तुम्हाला (?:नक्की )?मिळेल"]
VERDICT_RX = [re.compile(v, re.I) for v in VERDICTS]


@dataclass
class Grounding:
    ok: bool
    text: str = ""                                         # the cleaned text (links removed) when ok
    problems: list[str] = field(default_factory=list)


def numbers_in(text: str) -> set[str]:
    out = set()
    for n in NUMBER.findall(normalize_text(CITATION.sub(" ", text))):
        n = n.replace(",", "").rstrip(".")
        out.add(n.lstrip("0") or "0" if "." not in n else n)
    return out


def devanagari_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum("ऀ" <= c <= "ॿ" for c in letters) / len(letters) if letters else 0.0


def check(text: str, passages: list[dict], *, language: str, question_scheme_ids: tuple[str, ...] = ()) -> Grounding:
    problems: list[str] = []
    text = (text or "").strip()
    if not text or text.upper().startswith("INSUFFICIENT"):
        return Grounding(False, problems=["model said the passages are insufficient" if text else "empty"])
    cited = [int(n) for n in CITATION.findall(text)]
    if not cited:
        problems.append("no citations")
    bad = [n for n in cited if not 1 <= n <= len(passages)]
    if bad:
        problems.append(f"citation to a passage that does not exist: {sorted(set(bad))}")
    used = [passages[n - 1] for n in set(cited) if 1 <= n <= len(passages)]
    allowed_numbers = set().union(*(numbers_in(p["text"]) for p in used)) if used else set()
    stray = numbers_in(text) - allowed_numbers
    if stray:
        problems.append(f"numbers not in the cited passages: {sorted(stray)}")
    # every sentence must end with a citation: the text must end with one, and no segment between citations may
    # contain a sentence end of its own ("Rs." and similar abbreviations are not sentence ends)
    segments = CITATION.split(text)[0::2]
    if re.search(r"\w", segments[-1]) or any(SENTENCE_END.search(seg.lstrip(" .!?।")) for seg in segments[:-1]):
        problems.append("a sentence has no citation")
    allowed_schemes = {p["scheme_id"] for p in passages} | set(question_scheme_ids)
    other = set(find_scheme_mentions(text)) - allowed_schemes
    if other:
        problems.append(f"mentions a scheme that was not retrieved: {sorted(other)}")
    if any(rx.search(text) for rx in VERDICT_RX):
        problems.append("gives an eligibility verdict or a promise")
    if language in ("hi", "mr") and devanagari_share(CITATION.sub("", text)) < 0.5:
        problems.append("not written in the requested language")
    if language == "en" and devanagari_share(text) > 0.2:
        problems.append("not written in the requested language")
    if URLISH.search(text):
        problems.append("contained a web address")        # links come only from the records, never from the model
    return Grounding(not problems, text if not problems else "", problems)
