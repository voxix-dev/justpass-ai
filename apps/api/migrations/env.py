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
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    # Tests pass their own database in config.attributes.
    url = config.attributes.get("database_url") or os.environ["DATABASE_URL"]
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline mode (--sql) is not set up.")
asyncio.run(main())
