import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


async def _table_names(url: str) -> set[str]:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    await engine.dispose()
    return set(names)


def test_downgrade_removes_everything_and_upgrade_restores_it(
    alembic_config: Config, database_url: str
):
    tables = {"users"}
    command.downgrade(alembic_config, "base")
    assert asyncio.run(_table_names(database_url)) == {"alembic_version"}
    command.upgrade(alembic_config, "head")
    assert asyncio.run(_table_names(database_url)) == tables | {"alembic_version"}


def test_migrations_match_the_tables_in_code(alembic_config: Config):
    command.check(alembic_config)  # fails if autogenerate would create a new migration
