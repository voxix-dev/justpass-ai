"""jobs, job_rounds, artifacts

Revision ID: 0002
Revises: 0001

Unnamed constraints get their names from the naming convention in api/tables.py.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = "'received', 'processing', 'awaiting_review', 'accepted', 'expired', 'failed'"
ROLES = "'orchestrator', 'research', 'writer', 'solver', 'review', 'curator'"
KINDS = "'research_notes', 'outline', 'draft', 'review', 'final', 'test_summary'"


def timestamp(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(100), nullable=False),
        sa.Column("level", sa.String(32), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("input_text", sa.String()),
        sa.Column("photo_keys", postgresql.ARRAY(sa.String()), server_default="{}", nullable=False),
        sa.Column("review_deadline", sa.DateTime(timezone=True)),
        sa.Column("grade", sa.String(32)),
        sa.Column("grade_comment", sa.String()),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="status"),
        sa.CheckConstraint("level IN ('high_school', 'college')", name="level"),
        sa.CheckConstraint(
            "input_text IS NOT NULL OR cardinality(photo_keys) > 0", name="has_input"
        ),
    )
    op.create_index("ix_jobs_user_id_updated_at", "jobs", ["user_id", "updated_at"])
    op.create_index(
        "ix_jobs_review_deadline",
        "jobs",
        ["review_deadline"],
        postgresql_where=sa.text("status = 'awaiting_review'"),
    )
    op.create_table(
        "job_rounds",
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("number", sa.Integer(), primary_key=True),
        sa.Column("feedback", sa.String()),
        timestamp("created_at"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.CheckConstraint("number >= 1", name="number_positive"),
        sa.CheckConstraint("(number = 1) = (feedback IS NULL)", name="feedback_iff_not_first"),
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content", sa.String()),
        sa.Column("storage_key", sa.String()),
        timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["job_id", "round"], ["job_rounds.job_id", "job_rounds.number"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(f"role IN ({ROLES})", name="role"),
        sa.CheckConstraint(f"kind IN ({KINDS})", name="kind"),
        sa.CheckConstraint(
            "num_nonnulls(content, storage_key) = 1", name="content_xor_storage_key"
        ),
    )
    op.create_index("ix_artifacts_job_id_round", "artifacts", ["job_id", "round"])
    op.create_index(
        "uq_artifacts_one_final_per_round",
        "artifacts",
        ["job_id", "round"],
        unique=True,
        postgresql_where=sa.text("kind = 'final'"),
    )


def downgrade() -> None:
    # Dropping a table drops its indexes too.
    for table in ("artifacts", "job_rounds", "jobs"):
        op.drop_table(table)
