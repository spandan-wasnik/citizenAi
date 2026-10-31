"""Validate every scheme data file without touching the database.

    python scripts/validate_schemes.py [directory]

Exits non-zero if any file is invalid. Files whose names start with '_' are templates and skipped.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.scheme_loader import load_registry, load_scheme_dir  # noqa: E402


def main(argv: list[str]) -> int:
    directory = Path(argv[1]) if len(argv) > 1 else ROOT / "data" / "schemes"
    registry = load_registry(ROOT / "data" / "sources" / "registry.yaml")
    schemes, errors = load_scheme_dir(directory, registry)
    for s in schemes:
        print(f"[ OK ] {s.scheme_id}  ({s.verification_status}, rules_complete={s.rules_complete})")
    for e in errors:
        print(f"[FAIL] {Path(e.path).name}")
        for msg in e.messages:
            print(f"         - {msg}")
    print(f"\n{len(schemes)} valid, {len(errors)} invalid")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
