"""users

Revision ID: 0001
Revises:

Unnamed constraints get their names from the naming convention in api/tables.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the users table with a creation timestamp that defaults to the database's now().

    Database errors propagate to the caller.
    """
    op.create_table(
        "users",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("is_anonymous", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    """Drop the users table and all of its data.

    Database errors propagate to the caller.
    """
    op.drop_table("users")
