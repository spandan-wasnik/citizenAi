"""Initialise the database and load scheme data files into it.

    python scripts/seed_database.py [--force]

Invalid files stop the run before anything is written. --force allows overwriting a scheme
that is human_reviewed in the database; review the diff first.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.database.connection import make_engine, session_scope  # noqa: E402
from app.services.scheme_loader import SeedConflict, load_registry, load_scheme_dir, seed_schemes  # noqa: E402
from scripts.init_db import init_database  # noqa: E402


def main(argv: list[str]) -> int:
    force = "--force" in argv
    registry = load_registry(ROOT / "data" / "sources" / "registry.yaml")
    schemes, errors = load_scheme_dir(ROOT / "data" / "schemes", registry)
    if errors:
        for e in errors:
            print(f"[FAIL] {Path(e.path).name}: " + " | ".join(e.messages))
        print("No data was written: fix the files above first (python scripts/validate_schemes.py).")
        return 1
    url = get_settings().database_url
    init_database(url)
    engine = make_engine(url)
    try:
        with session_scope(engine) as session:
            result = seed_schemes(session, schemes, registry, force=force)
    except SeedConflict as exc:
        print(f"[CONFLICT] {exc}\nNothing was written.")
        return 2
    finally:
        engine.dispose()
    print(f"created={len(result.created)} updated={len(result.updated)} unchanged={len(result.unchanged)}")
    for w in result.warnings:
        print(f"[warn] {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
