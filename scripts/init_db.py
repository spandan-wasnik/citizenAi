"""Create or upgrade the database to the latest schema and seed the field registry.

    python scripts/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database.connection import make_engine, session_scope  # noqa: E402
from app.database.profile_fields import seed_profile_fields  # noqa: E402


def init_database(url: str | None = None) -> None:
    url = url or get_settings().database_url
    engine = make_engine(url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    with session_scope(engine) as session:
        seed_profile_fields(session)
    engine.dispose()


if __name__ == "__main__":
    init_database()
    print("Database is up to date.")
