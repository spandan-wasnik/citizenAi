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

USABLE = ("explicit", "confirmed")


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
