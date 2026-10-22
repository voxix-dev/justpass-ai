"""memory_docs, with full-text search

Revision ID: 0003
Revises: 0002

Unnamed constraints get their names from the naming convention in api/tables.py.
Autogenerate does not compare the tsv expression: change it here and in api/tables.py together.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The two-argument to_tsvector: the one-argument form is not IMMUTABLE, so Postgres rejects it.
TSV = "to_tsvector('simple'::regconfig, coalesce(subject, '') || ' ' || body)"


def upgrade() -> None:
    op.create_table(
        "memory_docs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("subject", sa.String(100)),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("tsv", postgresql.TSVECTOR(), sa.Computed(TSV, persisted=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        # One profile doc (subject NULL) and one doc per subject for each user.
        sa.UniqueConstraint("user_id", "subject", postgresql_nulls_not_distinct=True),
    )
    op.create_index("ix_memory_docs_tsv", "memory_docs", ["tsv"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("memory_docs")
