"""The database session the routes use. Tests replace get_session with their own session."""

import os
from collections.abc import AsyncIterator
from functools import cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine


@cache
def _engine() -> AsyncEngine:
    """Return a cached async engine, reading DATABASE_URL on the first successful call.

    Raise RuntimeError if the URL is unset or empty; engine configuration errors propagate.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Set DATABASE_URL (postgresql+asyncpg://...), e.g. `--env-file .env`.")
    return create_async_engine(url)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session that keeps loaded attributes after commit, then close it.

    The caller manages commits; uncommitted work is rolled back on close. A missing or empty
    DATABASE_URL raises RuntimeError; engine and session errors propagate.
    """
    async with AsyncSession(_engine(), expire_on_commit=False) as session:
        yield session
