"""
Alembic environment.

The database URL is taken from app/config.py's `Settings` — the same
object the running API uses — rather than from alembic.ini. That means:

  * migrations always target whatever `.env` points at, so there is no
    second place to keep the connection string in sync, and
  * no credential is ever written into alembic.ini (a committed file).

`target_metadata` is the application's own `Base.metadata`, so
`alembic revision --autogenerate` compares live models against the live
database.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

# Make the `app` package importable when alembic is invoked from the
# backend directory (alembic.ini also sets prepend_sys_path).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
from app import models  # noqa: E402,F401  - imported for its side effect of populating Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url():
    return settings.resolved_database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (`alembic upgrade --sql`)."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a real connection."""
    connectable = create_engine(_url(), pool_pre_ping=True)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Catch column type drift as well as added/removed columns.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
