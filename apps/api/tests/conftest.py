"""Database fixtures. Tests that use them need TEST_DATABASE_URL (see .env.example) and are skipped
without it. Each test run creates a fresh database on that server and drops it at the end."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[3]


async def _run_outside_transaction(url: str, sql: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.execute(text(sql))
    await engine.dispose()


def _alembic_config(url: str) -> Config:
    config = Config(toml_file=ROOT / "pyproject.toml")
    config.attributes["database_url"] = url
    return config


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A new database with all migrations applied."""
    server_url = os.environ.get("TEST_DATABASE_URL")
    if not server_url:
        pytest.skip("TEST_DATABASE_URL is not set; start Postgres with `docker compose up -d db`")
    name = f"test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_run_outside_transaction(server_url, f"CREATE DATABASE {name}"))
    url = make_url(server_url).set(database=name).render_as_string(hide_password=False)
    command.upgrade(_alembic_config(url), "head")
    yield url
    asyncio.run(_run_outside_transaction(server_url, f"DROP DATABASE {name} WITH (FORCE)"))


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    return _alembic_config(database_url)


@pytest.fixture(scope="session")
def sessionmaker(database_url: str) -> async_sessionmaker[AsyncSession]:
    # NullPool: no connection outlives the event loop of the test that opened it.
    engine = create_async_engine(database_url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def session(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session
