"""Report language-detection accuracy and a confusion matrix.

    python -m scripts.eval_language [path/to/gold.json]

The gold file is a JSON list of {"text": ..., "language": "en"|"hi"|"mr"|null}. Add real messages from real users
to eval/language_gold.json to make the numbers meaningful: the shipped set was written by the developer.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from app.nlp.langdetect import detect_language

DEFAULT = Path(__file__).resolve().parents[1] / "eval" / "language_gold.json"


def evaluate(cases: list[dict]) -> dict:
    confusion: Counter = Counter()
    wrong = []
    for c in cases:
        got = detect_language(c["text"]).language
        confusion[(c["language"], got)] += 1
        if got != c["language"]:
            wrong.append({"text": c["text"], "expected": c["language"], "got": got})
    n = len(cases)
    return {"n": n, "accuracy": (n - len(wrong)) / n if n else 0.0, "confusion": confusion, "wrong": wrong}


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT
    r = evaluate(json.loads(path.read_text(encoding="utf-8")))
    print(f"{r['n']} cases, accuracy {r['accuracy']:.1%}")
    labels = ["en", "hi", "mr", None]
    print("expected \\ got  " + "  ".join(f"{str(l):>5}" for l in labels))
    for e in labels:
        print(f"{str(e):>14}  " + "  ".join(f"{r['confusion'][(e, g)]:>5}" for g in labels))
    for w in r["wrong"]:
        print("WRONG:", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
