import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.tables import User


@pytest.mark.anyio
async def test_user_round_trips_with_a_server_side_created_at(session: AsyncSession):
    session.add(User(id="firebase-uid-1", is_anonymous=True))
    await session.flush()
    user = await session.scalar(select(User).where(User.id == "firebase-uid-1"))
    assert user is not None
    assert user.created_at.tzinfo is not None
