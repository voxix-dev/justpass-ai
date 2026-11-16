"""files

Revision ID: 0004
Revises: 0003

Unnamed constraints get their names from the naming convention in api/tables.py.
jobs.photo_keys keeps its name (the model calls it file_keys): the previous revision still serves
while this one migrates.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create files. Database errors propagate to the caller."""
    op.create_table(
        "files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("extracted_text", sa.String()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_files_user_id", "files", ["user_id"])


def downgrade() -> None:
    """Drop files with its rows. Database errors propagate to the caller."""
    # Dropping a table drops its indexes too.
    op.drop_table("files")
