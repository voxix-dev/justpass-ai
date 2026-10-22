import pytest
from pydantic import ValidationError

from shared.models import TRANSITIONS, JobStatus, ResearchBrief, can_transition


def test_every_status_has_transitions_defined():
    assert set(TRANSITIONS) == set(JobStatus)


def test_feedback_returns_job_to_processing():
    assert can_transition(JobStatus.AWAITING_REVIEW, JobStatus.PROCESSING)
    assert can_transition(JobStatus.EXPIRED, JobStatus.PROCESSING)


def test_finished_jobs_are_final():
    assert not can_transition(JobStatus.ACCEPTED, JobStatus.PROCESSING)
    assert not can_transition(JobStatus.FAILED, JobStatus.PROCESSING)


def test_research_brief_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ResearchBrief.model_validate(
            {
                "topic": "Causes of World War I",
                "questions": ["What role did alliances play?"],
                "level": "high_school",
                "language": "en",
                "length_words": 800,
                "student_city": "Kraków",
            }
        )
