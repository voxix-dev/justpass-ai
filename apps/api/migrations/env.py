"""Alembic runs this for every command. The database URL comes from $DATABASE_URL."""

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from api.tables import Base

config = context.config


def run_migrations(connection: Connection) -> None:
    """Run the configured Alembic operation on an open synchronous connection.

    Database and migration errors propagate to the caller.
    """
    context.configure(
        connection=connection, target_metadata=Base.metadata, compare_server_default=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    """Run the configured Alembic operation against the selected database.

    A non-empty config.attributes["database_url"] overrides DATABASE_URL.
    Raise SystemExit if neither supplies a URL. Invalid URL, connection, and
    migration errors propagate to the caller.
    """
    # Tests pass their own database in config.attributes.
    url = config.attributes.get("database_url") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_URL (postgresql+asyncpg://...), e.g. `--env-file .env`.")
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline mode (--sql) is not set up.")
asyncio.run(main())
