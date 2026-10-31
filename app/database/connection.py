"""Engine and session helpers. SQLite gets foreign keys, WAL and a busy timeout."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def _configure_sqlite(dbapi_connection, _record) -> None:  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def make_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    if url.startswith("sqlite") and ":memory:" not in url:
        path = url.split("sqlite:///", 1)[-1]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _configure_sqlite)
        if ":memory:" not in url:
            with engine.connect() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    return engine


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Commit on success, roll back on error."""
    session = sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
