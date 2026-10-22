import asyncio
from collections.abc import Callable

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from api.tables import Base

TABLES = {"users"}

# What `alembic check` does not compare: CHECK contents, partial-index WHERE clauses, defaults
# and generated-column expressions.
CATALOG = """
SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint
  WHERE connamespace = 'public'::regnamespace
UNION ALL SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'
UNION ALL SELECT c.relname, a.attname, pg_get_expr(d.adbin, d.adrelid) FROM pg_attrdef d
  JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
  JOIN pg_class c ON c.oid = d.adrelid
"""


async def _table_names(url: str) -> set[str]:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    await engine.dispose()
    return set(names) - {"alembic_version"}


async def _catalog(url: str, create_all: bool = False) -> set[tuple[str, ...]]:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as connection:
        if create_all:
            await connection.run_sync(Base.metadata.create_all)
        rows = (await connection.execute(text(CATALOG))).all()
    await engine.dispose()
    return {tuple(row) for row in rows if row[0] != "alembic_version"}


def test_migrations_apply_from_scratch_and_downgrade(
    alembic_config: Callable[[str], Config], empty_database_url: str
):
    config = alembic_config(empty_database_url)
    command.upgrade(config, "head")
    assert asyncio.run(_table_names(empty_database_url)) == TABLES
    command.downgrade(config, "base")
    assert asyncio.run(_table_names(empty_database_url)) == set()
    command.upgrade(config, "head")
    assert asyncio.run(_table_names(empty_database_url)) == TABLES


def test_migrations_match_the_tables_in_code(
    alembic_config: Callable[[str], Config], database_url: str, empty_database_url: str
):
    command.check(alembic_config(database_url))  # columns, types, nullability, indexes
    migrated = asyncio.run(_catalog(database_url))
    assert migrated == asyncio.run(_catalog(empty_database_url, create_all=True))
