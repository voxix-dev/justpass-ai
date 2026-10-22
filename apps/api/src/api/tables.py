"""Postgres tables. Only the api package touches the database: the agent goes through the Context
API, and anything that leaves the api is converted to a Pydantic model first.

After changing a table run `uv run alembic revision --autogenerate -m "what changed"` and read the
generated migration. Autogenerate does not see changes to computed columns or to the values of an
enum column: write those by hand.

There are no relationship()s, so the session does not know which row to insert first: add the
parent row (e.g. the user) and flush it before adding rows that point to it.
"""

from datetime import datetime

from sqlalchemy import DateTime, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    # Read once when the class is created, never mutated.
    type_annotation_map = {datetime: DateTime(timezone=True)}  # noqa: RUF012
    # Fixed constraint names, so later migrations can find and drop them.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class User(Base):
    __tablename__ = "users"

    # Firebase uid. It stays the same when an anonymous user links Google or email.
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    is_anonymous: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
