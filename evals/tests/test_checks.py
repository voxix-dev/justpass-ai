import pytest

from evals.checks import (
    answers_match,
    check_final_answer,
    check_length,
    check_math_syntax,
    last_boxed,
    run_checks,
)
from evals.models import Criterion, Sample, Source, Subject


@pytest.mark.parametrize(
    ("got", "expected"),
    [
        ("5", "5"),
        ("x = 5", "5"),
        (r"\dfrac{1}{2}", "0.5"),
        (r"\frac12", r"\frac{1}{2}"),
        ("1,000", "1000"),
        ("1{,}000", "1000"),
        ("f'(1) = 1", "1"),
        (r"P(A)=\frac16", "1/6"),
        (r"4\sqrt2 \approx 5.66", r"4\sqrt{2}"),
        (r"200\ \text{USD}", "200"),
        (r"\$200", "200"),
        (r"-\frac{3}{4}", "-0.75"),
        (r"\left( 1, 2 \right)", "(1,2)"),
        (r"4\sqrt{2}", r"4 \sqrt{2}"),
        ("200.", "200"),
    ],
)
def test_answers_match(got: str, expected: str):
    assert answers_match(got, expected)


@pytest.mark.parametrize(
    ("got", "expected"),
    [("6", "5"), (r"\frac{1}{3}", "0.33"), ("2,5", "25"), ("1,000", "1"), ("2,5", "2.5")],
)
def test_answers_differ(got: str, expected: str):
    assert not answers_match(got, expected)


@pytest.mark.parametrize(("got", "expected"), [("2,5", "2.5"), ("2{,}5", "2.5"), ("0,125", "1/8")])
def test_decimal_comma(got: str, expected: str):
    assert answers_match(got, expected, decimal_comma=True)


def test_last_boxed_handles_nested_braces():
    assert last_boxed(r"first \boxed{1}, final $\boxed{\frac{1}{2}}$") == r"\frac{1}{2}"
    assert last_boxed("no box") is None
    assert last_boxed(r"\boxed{unclosed") is None
    assert last_boxed(r"\boxed{\{1\}}") == r"\{1\}"


def test_final_answer():
    assert check_final_answer(["5"], r"so $\boxed{x=5}$", False).passed
    wrong = check_final_answer(["5"], r"$\boxed{6}$", False)
    assert not wrong.passed and wrong.detail == "got 6"
    assert check_final_answer(["5"], "x = 5", False).detail == r"no \boxed{} answer"


@pytest.mark.parametrize(
    "text",
    [
        "no math at all",
        r"inline $\frac{a}{b}$ and $$\begin{aligned} x &= 1 \end{aligned}$$",
        r"costs \$5, and \(\left( x \right)\) or \[ \{1\} \]",
        "set `$PATH`\n```sh\necho $HOME\n```",
    ],
)
def test_math_syntax_ok(text: str):
    assert check_math_syntax(text).passed


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("costs $5", "unbalanced $"),
        ("$$a$", "unbalanced $"),
        ("I paid $5 and $10", "unbalanced $"),
        (r"$\frac{a}{b$", "unbalanced braces"),
        (r"$a}{b$", "unbalanced braces"),
        (r"$\left( x$", r"unbalanced \left/\right"),
        (r"$$\begin{cases} x \end{array}$$", r"unmatched \end{array}"),
        (r"$$\begin{cases} x $$", r"unclosed \begin{cases}"),
        ("$$ $$", "empty math"),
    ],
)
def test_math_syntax_problems(text: str, problem: str):
    result = check_math_syntax(text)
    assert not result.passed and result.detail.startswith(problem)


def test_length():
    assert check_length("one two three", 2, 3).passed
    assert not check_length("one", 2, None).passed
    assert not check_length("one two three", None, 2).passed


def test_run_checks_picks_checks_by_subject():
    essay = Sample(
        id="e", subject=Subject.ESSAY, level="college", language="en", prompt="?",
        rubric=[Criterion(description="thesis")], min_words=1, source=Source.SYNTHETIC,
    )  # fmt: skip
    math = Sample(
        id="m", subject=Subject.MATH, level="college", language="en", prompt="?",
        answers=["1"], source=Source.SYNTHETIC,
    )  # fmt: skip
    assert [c.name for c in run_checks(essay, "text")] == ["not_empty", "math_syntax", "length"]
    assert [c.name for c in run_checks(math, "")] == ["not_empty", "math_syntax", "final_answer"]
