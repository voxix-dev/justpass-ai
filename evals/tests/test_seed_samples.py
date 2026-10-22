from pathlib import Path

from evals.__main__ import DEFAULT_SAMPLES
from evals.dataset import load_samples
from evals.score import score_dir
from evals.solve import fake_solve, run


def test_seed_samples_load_and_cover_the_basics():
    samples = load_samples(DEFAULT_SAMPLES)
    assert {s.subject for s in samples} == {"math", "essay"}
    assert {s.level for s in samples} == {"high_school", "college"}
    assert sum(s.language != "en" for s in samples) >= 2


def test_fake_answers_pass_every_seed_check(tmp_path: Path):
    samples = load_samples(DEFAULT_SAMPLES)
    run(samples, fake_solve, tmp_path)
    report = score_dir(samples, tmp_path)
    assert report.checks_passed == report.checks_total
