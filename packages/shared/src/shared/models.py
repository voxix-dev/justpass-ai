from enum import StrEnum


class JobStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    AWAITING_REVIEW = "awaiting_review"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    FAILED = "failed"


# Feedback moves a job from review (or expired, via a fresh actor) back to processing.
TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.RECEIVED: frozenset({JobStatus.PROCESSING}),
    JobStatus.PROCESSING: frozenset({JobStatus.AWAITING_REVIEW, JobStatus.FAILED}),
    JobStatus.AWAITING_REVIEW: frozenset(
        {JobStatus.ACCEPTED, JobStatus.PROCESSING, JobStatus.EXPIRED}
    ),
    JobStatus.EXPIRED: frozenset({JobStatus.PROCESSING}),
    JobStatus.ACCEPTED: frozenset(),
    JobStatus.FAILED: frozenset(),
}


def can_transition(current: JobStatus, new: JobStatus) -> bool:
    return new in TRANSITIONS[current]


class Level(StrEnum):
    HIGH_SCHOOL = "high_school"
    COLLEGE = "college"


class ArtifactKind(StrEnum):
    """What an agent wrote. Each round has at most one FINAL: the answer the student reviews."""

    RESEARCH_NOTES = "research_notes"
    OUTLINE = "outline"
    DRAFT = "draft"
    REVIEW = "review"
    FINAL = "final"
    TEST_SUMMARY = "test_summary"
