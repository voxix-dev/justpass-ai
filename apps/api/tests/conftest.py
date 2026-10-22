"""Database fixtures. Tests that use them need TEST_DATABASE_URL (see .env.example) and are skipped
without it (in CI they fail instead). Every database they create is dropped at the end."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
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


@contextmanager
def _fresh_database() -> Iterator[str]:
    server_url = os.environ.get("TEST_DATABASE_URL")
    if not server_url:
        if os.environ.get("CI"):
            pytest.fail("TEST_DATABASE_URL must be set in CI")
        pytest.skip("TEST_DATABASE_URL is not set; start Postgres with `docker compose up -d db`")
    name = f"test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_run_outside_transaction(server_url, f"CREATE DATABASE {name}"))
    try:
        yield make_url(server_url).set(database=name).render_as_string(hide_password=False)
    finally:
        asyncio.run(_run_outside_transaction(server_url, f"DROP DATABASE {name} WITH (FORCE)"))


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A database with all migrations applied, shared by the whole test run."""
    with _fresh_database() as url:
        command.upgrade(_alembic_config(url), "head")
        yield url


@pytest.fixture
def empty_database_url() -> Iterator[str]:
    """A database with no tables, for this test only."""
    with _fresh_database() as url:
        yield url


@pytest.fixture
def alembic_config() -> Callable[[str], Config]:
    return _alembic_config


@pytest.fixture(scope="session")
def engine(database_url: str) -> AsyncEngine:
    # NullPool: no connection outlives the event loop of the test that opened it.
    return create_async_engine(database_url, poolclass=NullPool)


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Everything the test writes is rolled back afterwards, even if it calls commit()."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as session:
            yield session
        await transaction.rollback()
