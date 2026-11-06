"""Optional model-written answer on top of the extractive one. Falls back to the extractive answer on any problem."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.grounding import check
from app.ai.llm import LLMProvider, LLMUnavailable, build_prompt


@dataclass
class Generated:
    text: str | None
    provider: str | None
    fallback_reason: str | None = None


def generate(provider: LLMProvider | None, question: str, passages: list[dict[str, Any]], *, language: str,
             scheme_ids: tuple[str, ...] = ()) -> Generated:
    if provider is None:
        return Generated(None, None, "no language model configured")
    if not passages:
        return Generated(None, provider.name, "no evidence to write from")
    system, user = build_prompt(question, passages, language)
    try:
        raw = provider.generate(system, user)
    except LLMUnavailable as e:
        return Generated(None, provider.name, f"language model unavailable ({e})")
    g = check(raw, passages, language=language, question_scheme_ids=scheme_ids)
    if not g.ok:
        return Generated(None, provider.name, "failed grounding check: " + "; ".join(g.problems))
    return Generated(g.text, provider.name)
