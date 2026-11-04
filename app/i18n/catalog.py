"""Text catalogs for English, Hindi and Marathi, loaded from app/i18n/data/<lang>.json.

The files are plain JSON so a native speaker can review and correct them in any text editor; tests check that all
three have exactly the same keys and the same {placeholders}, so a translation can never silently go missing.
Questions are worded without gendered verb forms where the language allows it, so they suit any user.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

LANGS = ("en", "hi", "mr")
DATA = Path(__file__).parent / "data"
# Terms that must appear exactly as written in every language (official category names), never be translated.
PROTECTED_TERMS = ("SC", "ST", "OBC", "EWS")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


class CatalogError(RuntimeError):
    pass


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, Any]:
    if lang not in LANGS:
        raise CatalogError(f"no text catalog for language '{lang}'; refusing to fall back to another language silently")
    return json.loads((DATA / f"{lang}.json").read_text(encoding="utf-8"))


def message(lang: str, key: str, **params: Any) -> str:
    try:
        template = catalog(lang)["messages"][key]
    except KeyError:
        raise CatalogError(f"missing message '{key}' for '{lang}'") from None
    return template.format(**params)


def placeholders(template: str) -> set[str]:
    return set(PLACEHOLDER.findall(template))
