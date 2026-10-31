"""Alembic environment. URL precedence: -x/attributes url, then DATABASE_URL from settings."""
from alembic import context
from sqlalchemy import create_engine, event

from app.config import get_settings
from app.database.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        engine = create_engine(_url())
        if engine.dialect.name == "sqlite":
            event.listen(engine, "connect", lambda c, _r: c.execute("PRAGMA foreign_keys=ON"))
        with engine.connect() as connection:
            _run(connection)
    else:
        _run(connectable)


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
