"""Producing outputs. The real solver (the agent harness, reached over HTTP) plugs in later
through the Solver interface; until then only the fake one exists."""

from pathlib import Path
from typing import Protocol

from evals.models import Sample, Subject


class Solver(Protocol):
    def __call__(self, sample: Sample) -> str:
        """Return the answer as Markdown with KaTeX math."""
        ...


def fake_solve(sample: Sample) -> str:
    """A perfect-looking answer built from the sample itself. Tests the pipeline, not a model."""
    if sample.subject == Subject.MATH:
        return f"Worked solution.\n\n$$\\boxed{{{sample.answers[0]}}}$$\n"
    return " ".join(["word"] * (sample.min_words or 100)) + "\n"


def run(samples: list[Sample], solve: Solver, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for sample in samples:
        (out_dir / f"{sample.id}.md").write_text(solve(sample), encoding="utf-8")
