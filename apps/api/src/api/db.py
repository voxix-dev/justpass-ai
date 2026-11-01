"""The database session the routes use. Tests replace get_session with their own session."""

import os
from collections.abc import AsyncIterator
from functools import cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine


@cache
def _engine() -> AsyncEngine:
    """The process-wide engine for $DATABASE_URL, created on first use."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Set DATABASE_URL (postgresql+asyncpg://...), e.g. `--env-file .env`.")
    # pool_pre_ping survives a database restart. The timeouts bound a hung database: without them
    # a request waits forever and the student sees a spinner.
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args={"timeout": 5, "command_timeout": 10},
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """A session for one request; the route commits what it changes."""
    async with AsyncSession(_engine(), expire_on_commit=False) as session:
        yield session
