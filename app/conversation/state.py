"""Conversation state: what we have been told so far, and how sure we are of each answer.

Privacy: only values and their provenance are kept. The person's own words are never stored in the state, and the
state lives in the caller's session memory only (nothing here touches a disk or database).

Provenance of an answer:
  explicit  - the person said it about themselves
  confirmed - we inferred it and the person then confirmed it
  inferred  - we derived it and it is still waiting for confirmation; NEVER used for eligibility until confirmed
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.fields import FIELD_TYPES, allowed_values
from app.i18n.catalog import LANGS

USABLE = ("explicit", "confirmed")
PROVENANCES = ("explicit", "confirmed", "inferred")
MAX_ITEMS = 30
MAX_STR = 100


@dataclass
class Answer:
    value: Any
    provenance: str


@dataclass
class ConversationState:
    language: str = "en"
    answers: dict[str, Answer] = field(default_factory=dict)
    pending_confirm: list[str] = field(default_factory=list)   # fields holding an inferred value, oldest first
    skipped: set[str] = field(default_factory=set)             # questions the person chose not to answer
    expected_field: str | None = None                          # the question currently waiting for an answer
    fail_counts: dict[str, int] = field(default_factory=dict)  # how often a question was not understood
    pending_income: int | None = None                          # an income amount waiting to learn whose it is
    focus: list[str] = field(default_factory=list)             # schemes the person is asking about, if any
    turns: int = 0

    def profile(self) -> dict[str, Any]:
        """The answers that may be used for eligibility: explicit and confirmed only."""
        return {f: a.value for f, a in self.answers.items() if a.provenance in USABLE}

    def to_dict(self) -> dict[str, Any]:
        return {"language": self.language, "answers": {f: [a.value, a.provenance] for f, a in self.answers.items()},
                "pending_confirm": list(self.pending_confirm), "skipped": sorted(self.skipped),
                "expected_field": self.expected_field, "fail_counts": dict(self.fail_counts),
                "pending_income": self.pending_income, "focus": list(self.focus), "turns": self.turns}

    @classmethod
    def from_untrusted(cls, d: Any) -> "ConversationState":
        """Build a state from data a client sent back to us. Anything that is not exactly what we would have produced is
        rejected (ValueError, with no echo of the content): the client cannot smuggle in unknown fields, wrong types,
        or oversized values."""
        def bad(msg: str):
            raise ValueError(msg)

        if not isinstance(d, dict):
            bad("state must be an object")
        known = {"language", "answers", "pending_confirm", "skipped", "expected_field", "fail_counts", "pending_income",
                 "focus", "turns"}
        if set(d) - known:
            bad("unknown state keys")
        if d.get("language", "en") not in LANGS:
            bad("bad language")
        answers = d.get("answers", {})
        if not isinstance(answers, dict) or len(answers) > MAX_ITEMS:
            bad("bad answers")
        clean: dict[str, Answer] = {}
        for f, pair in answers.items():
            if f not in FIELD_TYPES or not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                bad("bad answer")
            v, prov = pair
            if prov not in PROVENANCES:
                bad("bad provenance")
            t = FIELD_TYPES[f]
            if t == "bool":
                ok = isinstance(v, bool)
            elif t in ("int", "money_inr"):
                ok = isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10**10
            elif t == "enum":
                ok = isinstance(v, str) and v in (allowed_values(f) or ())
            else:
                ok = isinstance(v, str) and len(v) <= MAX_STR
            if not ok:
                bad("bad answer value")
            clean[f] = Answer(v, prov)
        pending = d.get("pending_confirm", [])
        if not (isinstance(pending, list) and len(pending) <= MAX_ITEMS
                and all(isinstance(f, str) and f in clean and clean[f].provenance == "inferred" for f in pending)):
            bad("bad pending_confirm")
        skipped = d.get("skipped", [])
        if not (isinstance(skipped, list) and len(skipped) <= MAX_ITEMS and all(isinstance(f, str) and f in FIELD_TYPES for f in skipped)):
            bad("bad skipped")
        exp = d.get("expected_field")
        if exp is not None and exp not in FIELD_TYPES:
            bad("bad expected_field")
        fails = d.get("fail_counts", {})
        if not (isinstance(fails, dict) and len(fails) <= MAX_ITEMS and all(
                k in FIELD_TYPES and isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 1000 for k, v in fails.items())):
            bad("bad fail_counts")
        inc = d.get("pending_income")
        if inc is not None and not (isinstance(inc, int) and not isinstance(inc, bool) and 0 <= inc <= 10**10):
            bad("bad pending_income")
        focus = d.get("focus", [])
        if not (isinstance(focus, list) and len(focus) <= 5 and all(isinstance(x, str) and 0 < len(x) <= MAX_STR for x in focus)):
            bad("bad focus")
        turns = d.get("turns", 0)
        if not (isinstance(turns, int) and not isinstance(turns, bool) and 0 <= turns <= 100000):
            bad("bad turns")
        return cls(language=d.get("language", "en"), answers=clean, pending_confirm=list(pending), skipped=set(skipped),
                   expected_field=exp, fail_counts=dict(fails), pending_income=inc, focus=list(focus), turns=turns)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConversationState":
        return cls(language=d.get("language", "en"), answers={f: Answer(v[0], v[1]) for f, v in d.get("answers", {}).items()},
                   pending_confirm=list(d.get("pending_confirm", [])), skipped=set(d.get("skipped", [])),
                   expected_field=d.get("expected_field"), fail_counts=dict(d.get("fail_counts", {})),
                   pending_income=d.get("pending_income"), focus=list(d.get("focus", [])), turns=int(d.get("turns", 0)))


@dataclass(frozen=True)
class Action:
    """What the system should do or say next. The renderer turns actions into words in the person's language."""
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
