"""The database session the routes use. Tests replace get_session with their own session."""

import os
from collections.abc import AsyncIterator
from functools import cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine


@cache
def _engine() -> AsyncEngine:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Set DATABASE_URL (postgresql+asyncpg://...), e.g. `--env-file .env`.")
    return create_async_engine(url)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(_engine(), expire_on_commit=False) as session:
        yield session
