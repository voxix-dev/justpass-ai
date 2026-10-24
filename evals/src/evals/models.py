from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class Subject(StrEnum):
    MATH = "math"
    ESSAY = "essay"


class Source(StrEnum):
    SYNTHETIC = "synthetic"
    REAL = "real"  # anonymised student homework, see evals/README.md


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    points: int = Field(default=1, gt=0, le=10)


class Sample(BaseModel):
    """One homework task and what a good answer must contain. One YAML file per sample."""

    # coerce: `answers: [5]` in YAML is an int; accept it as "5".
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    subject: Subject
    level: Literal["high_school", "college"]
    language: str = Field(min_length=2, max_length=8)
    prompt: str = Field(min_length=1)
    # Relative to the sample file in YAML; the loader makes it absolute.
    image: Path | None = None
    # Accepted final answers (math). Any one of them matching counts.
    answers: list[str] = []
    rubric: list[Criterion] = []
    min_words: int | None = Field(default=None, gt=0)
    max_words: int | None = Field(default=None, gt=0)
    source: Source

    @model_validator(mode="after")
    def _has_expectations(self) -> Self:
        if self.subject == Subject.MATH and not self.answers:
            raise ValueError("math samples need at least one accepted answer")
        if self.subject == Subject.ESSAY and not self.rubric:
            raise ValueError("essay samples need a rubric")
        if self.min_words and self.max_words and self.min_words > self.max_words:
            raise ValueError("min_words is larger than max_words")
        return self


class Judgement(BaseModel):
    """What an LLM judge returns: points per rubric criterion, in rubric order."""

    points: list[int]
    reason: str = Field(max_length=2000)


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class SampleResult(BaseModel):
    sample_id: str
    subject: Subject
    level: str
    language: str
    checks: list[CheckResult]
    rubric_points: int | None = None
    rubric_max: int | None = None
    judge_reason: str = ""

    @computed_field
    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


class Report(BaseModel):
    results: list[SampleResult]
    checks_passed: int
    checks_total: int
    rubric_score: float | None  # mean of points / max over judged samples
