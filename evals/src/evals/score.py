from pathlib import Path

from evals.checks import run_checks
from evals.judge import Judge, validate_judgement
from evals.models import CheckResult, Report, Sample, SampleResult


def score_sample(sample: Sample, output: str | None, judge: Judge | None) -> SampleResult:
    result = SampleResult(
        sample_id=sample.id,
        subject=sample.subject,
        level=sample.level,
        language=sample.language,
        checks=[],
    )
    if judge is not None and sample.rubric:
        result.rubric_points = 0  # a missing output scores zero, it doesn't drop out of the mean
        result.rubric_max = sum(c.points for c in sample.rubric)
    if output is None:
        result.checks = [CheckResult(name="output_exists", passed=False)]
        return result
    result.checks = run_checks(sample, output)
    if judge is not None and sample.rubric:
        judgement = judge(sample, output)
        validate_judgement(sample, judgement)
        result.rubric_points = sum(judgement.points)
        result.judge_reason = judgement.reason
    return result


def score_dir(samples: list[Sample], outputs_dir: Path, judge: Judge | None = None) -> Report:
    """Score <outputs_dir>/<sample id>.md for every sample. A missing file fails that sample."""
    results: list[SampleResult] = []
    for sample in samples:
        path = outputs_dir / f"{sample.id}.md"
        output = path.read_text(encoding="utf-8") if path.is_file() else None
        results.append(score_sample(sample, output, judge))
    checks = [c for r in results for c in r.checks]
    judged = [r for r in results if r.rubric_points is not None and r.rubric_max]
    return Report(
        results=results,
        checks_passed=sum(c.passed for c in checks),
        checks_total=len(checks),
        rubric_score=(
            sum((r.rubric_points or 0) / (r.rubric_max or 1) for r in judged) / len(judged)
            if judged
            else None
        ),
    )


def format_table(report: Report) -> str:
    rows = [["sample", "subject", "level", "lang", "checks", "rubric", "failed"]]
    for r in report.results:
        passed = sum(c.passed for c in r.checks)
        rubric = "-" if r.rubric_points is None else f"{r.rubric_points}/{r.rubric_max}"
        failed = [
            f"{c.name} ({c.detail})" if c.detail else c.name for c in r.checks if not c.passed
        ]
        checks = f"{passed}/{len(r.checks)}"
        rows.append(
            [r.sample_id, r.subject, r.level, r.language, checks, rubric, ", ".join(failed)]
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) for row in rows]
    rubric = "-" if report.rubric_score is None else f"{report.rubric_score:.0%}"
    lines.append(f"\nchecks passed: {report.checks_passed}/{report.checks_total}  rubric: {rubric}")
    return "\n".join(line.rstrip() for line in lines)
