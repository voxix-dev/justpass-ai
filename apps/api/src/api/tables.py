"""Postgres tables. Only the api package touches the database: the agent goes through the Context
API, and anything that leaves the api is converted to a Pydantic model first.

After changing a table run `uv run --env-file .env alembic revision --autogenerate --rev-id 0004
-m "what changed"` (next free number) and read the generated migration. Autogenerate does not see
changes to computed columns or to the values of an enum column: write those by hand.

There are no relationship()s, so the session does not know which row to insert first: add the
parent row (user, job, round) and flush it before adding rows that point to it.
"""

import uuid
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    MetaData,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from shared.models import ArtifactKind, JobStatus, Level, Role, can_transition

REVIEW_WINDOW = timedelta(hours=48)


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


def _values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


def str_enum(enum: type[StrEnum], name: str) -> Enum:
    """Stores the enum's values as text plus a CHECK on them (easier to change than a PG enum)."""
    return Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=_values,
    )


class InvalidTransition(ValueError):
    def __init__(self, current: JobStatus, new: JobStatus) -> None:
        super().__init__(f"a job cannot go from {current} to {new}")


class User(Base):
    __tablename__ = "users"

    # Firebase uid. It stays the same when an anonymous user links Google or email.
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    is_anonymous: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Job(Base):
    """One homework request."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("input_text IS NOT NULL OR cardinality(photo_keys) > 0", name="has_input"),
        CheckConstraint(
            "(status = 'awaiting_review') = (review_deadline IS NOT NULL)",
            name="deadline_iff_awaiting_review",
        ),
        Index(None, "user_id", "updated_at"),  # "my jobs" and "what changed since"
        Index(None, "review_deadline", postgresql_where=text("status = 'awaiting_review'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Change it with move_to(), never directly.
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "status"), default=JobStatus.RECEIVED
    )
    subject: Mapped[str] = mapped_column(String(100))
    level: Mapped[Level] = mapped_column(str_enum(Level, "level"))
    language: Mapped[str] = mapped_column(String(8))
    input_text: Mapped[str | None]
    # Cloud Storage object keys. The photos themselves never go in the database.
    photo_keys: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    # Feedback is accepted until then. Set when the job reaches awaiting_review.
    review_deadline: Mapped[datetime | None]
    # The grade the student says they got, and their comment. Both optional.
    grade: Mapped[str | None] = mapped_column(String(32))
    grade_comment: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Wall-clock time of the change (now() would be the transaction's start). Rows can still
    # commit a little after this time, so poll "updated since" with a few seconds of overlap.
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.clock_timestamp(), onupdate=func.clock_timestamp()
    )

    def move_to(self, new: JobStatus, now: datetime) -> None:
        """Changes the status if shared.models.TRANSITIONS allows it, else raises. Load the job with
        select(Job).with_for_update() first, so two requests cannot change it at the same time."""
        if not can_transition(self.status, new):
            raise InvalidTransition(self.status, new)
        self.status = new
        self.review_deadline = now + REVIEW_WINDOW if new == JobStatus.AWAITING_REVIEW else None


class JobRound(Base):
    """One agent run of a job. Round 1 answers the request; each feedback starts the next round.
    The round's result is its FINAL artifact."""

    __tablename__ = "job_rounds"
    __table_args__ = (
        CheckConstraint("number >= 1", name="number_positive"),
        CheckConstraint("(number = 1) = (feedback IS NULL)", name="feedback_iff_not_first"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    number: Mapped[int] = mapped_column(primary_key=True)
    feedback: Mapped[str | None]  # what the student asked to change
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Artifact(Base):
    """Something an agent wrote. Agents pass artifact ids to each other, not the content."""

    __tablename__ = "artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "round"], ["job_rounds.job_id", "job_rounds.number"], ondelete="CASCADE"
        ),
        CheckConstraint("num_nonnulls(content, storage_key) = 1", name="content_xor_storage_key"),
        Index(None, "job_id", "round"),
        Index(
            "uq_artifacts_one_final_per_round",
            "job_id",
            "round",
            unique=True,
            postgresql_where=text("kind = 'final'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID]
    round: Mapped[int]
    role: Mapped[Role] = mapped_column(str_enum(Role, "role"))
    kind: Mapped[ArtifactKind] = mapped_column(str_enum(ArtifactKind, "kind"))
    content: Mapped[str | None]  # Markdown, math as KaTeX
    storage_key: Mapped[str | None]  # Cloud Storage object key, for files
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
