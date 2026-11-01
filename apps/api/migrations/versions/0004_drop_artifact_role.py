"""drop artifacts.role

Revision ID: 0004
Revises: 0003

Unnamed constraints get their names from the naming convention in api/tables.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = "'orchestrator', 'research', 'writer', 'solver', 'review', 'curator'"


def upgrade() -> None:
    """Drop artifacts.role and all of its data. Postgres drops its CHECK with the column.

    Database errors propagate to the caller.
    """
    op.drop_column("artifacts", "role")


def downgrade() -> None:
    """Add artifacts.role back with its CHECK. Existing rows get 'writer'.

    Database errors propagate to the caller.
    """
    op.add_column(
        "artifacts", sa.Column("role", sa.String(32), server_default="writer", nullable=False)
    )
    op.create_check_constraint("role", "artifacts", f"role IN ({ROLES})")
    op.alter_column("artifacts", "role", server_default=None)
