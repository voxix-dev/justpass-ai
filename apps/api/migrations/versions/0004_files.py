"""files, and jobs.photo_keys renamed to file_keys

Revision ID: 0004
Revises: 0003

Unnamed constraints get their names from the naming convention in api/tables.py.
Autogenerate does not compare CHECK contents: change has_input here and in api/tables.py together.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def rename_not_null(old: str, new: str) -> None:
    """Rename a column's NOT NULL constraint, which Postgres 18 names and does not rename with it.

    Earlier Postgres versions have no such constraint, so nothing happens there.
    """
    op.execute(
        f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{old}') THEN
                ALTER TABLE jobs RENAME CONSTRAINT {old} TO {new};
            END IF;
        END $$
        """
    )


def upgrade() -> None:
    """Create files, rename jobs.photo_keys to file_keys keeping its data, and recheck has_input.

    Database errors propagate to the caller.
    """
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
    op.drop_constraint("has_input", "jobs", type_="check")
    op.alter_column("jobs", "photo_keys", new_column_name="file_keys")
    rename_not_null("jobs_photo_keys_not_null", "jobs_file_keys_not_null")
    op.create_check_constraint(
        "has_input", "jobs", "input_text IS NOT NULL OR cardinality(file_keys) > 0"
    )


def downgrade() -> None:
    """Rename jobs.file_keys back to photo_keys keeping its data, and drop files with its rows.

    Database errors propagate to the caller.
    """
    op.drop_constraint("has_input", "jobs", type_="check")
    op.alter_column("jobs", "file_keys", new_column_name="photo_keys")
    rename_not_null("jobs_file_keys_not_null", "jobs_photo_keys_not_null")
    op.create_check_constraint(
        "has_input", "jobs", "input_text IS NOT NULL OR cardinality(photo_keys) > 0"
    )
    # Dropping a table drops its indexes too.
    op.drop_table("files")
