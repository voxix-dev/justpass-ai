"""Deterministic checks: free, fast, and the same result every run."""

import re
from fractions import Fraction

from evals.models import CheckResult, Sample, Subject

# Inline $...$ follows pandoc: no space after the opening $, none before the closing $, and no
# digit right after it, so "costs $5 and $10" is not math.
_MATH_SPAN = re.compile(
    r"\$\$(.+?)\$\$|\$(?![\s$])([^$]+?)(?<!\s)\$(?!\d)|\\\[(.+?)\\\]|\\\((.+?)\\\)", re.DOTALL
)
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
_ENV = re.compile(r"\\(begin|end)\{([^}]*)\}")


def run_checks(sample: Sample, output: str) -> list[CheckResult]:
    checks = [check_not_empty(output), check_math_syntax(output)]
    if sample.subject == Subject.MATH:
        decimal_comma = sample.language != "en"
        checks.append(check_final_answer(sample.answers, output, decimal_comma))
    if sample.min_words or sample.max_words:
        checks.append(check_length(output, sample.min_words, sample.max_words))
    return checks


def check_not_empty(output: str) -> CheckResult:
    return CheckResult(name="not_empty", passed=bool(output.strip()))


def check_math_syntax(output: str) -> CheckResult:
    """A structural check that KaTeX would parse the math: delimiters, braces, environments and
    \\left/\\right pairs balance. Not a full KaTeX parse: unknown commands are not caught."""
    text = _CODE.sub("", output).replace(r"\$", "")
    if "$" in _MATH_SPAN.sub("", text):
        return CheckResult(name="math_syntax", passed=False, detail="unbalanced $ (escape as \\$)")
    for match in _MATH_SPAN.finditer(text):
        span = next(g for g in match.groups() if g is not None)
        if problem := _span_problem(span):
            return CheckResult(name="math_syntax", passed=False, detail=f"{problem}: {span[:60]}")
    return CheckResult(name="math_syntax", passed=True)


def _span_problem(span: str) -> str | None:
    if not span.strip():
        return "empty math"
    depth = 0
    for ch in re.sub(r"\\[{}]", "", span):
        depth += {"{": 1, "}": -1}.get(ch, 0)
        if depth < 0:
            return "unbalanced braces"
    if depth:
        return "unbalanced braces"
    if len(re.findall(r"\\left\b", span)) != len(re.findall(r"\\right\b", span)):
        return "unbalanced \\left/\\right"
    open_envs: list[str] = []
    for kind, name in _ENV.findall(span):
        if kind == "begin":
            open_envs.append(name)
        elif not open_envs or open_envs.pop() != name:
            return f"unmatched \\end{{{name}}}"
    return f"unclosed \\begin{{{open_envs[-1]}}}" if open_envs else None


def check_final_answer(answers: list[str], output: str, decimal_comma: bool) -> CheckResult:
    """The output must put its final answer in the last \\boxed{...}."""
    got = last_boxed(output)
    if got is None:
        return CheckResult(name="final_answer", passed=False, detail="no \\boxed{} answer")
    ok = any(answers_match(got, expected, decimal_comma) for expected in answers)
    return CheckResult(name="final_answer", passed=ok, detail="" if ok else f"got {got}")


def last_boxed(text: str) -> str | None:
    start = text.rfind(r"\boxed{")
    if start == -1:
        return None
    depth, i = 0, start + len(r"\boxed")
    while i < len(text):
        if text[i] == "\\":  # skip escaped characters such as \}
            i += 2
            continue
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start + len(r"\boxed{") : i]
        i += 1
    return None


def answers_match(got: str, expected: str, decimal_comma: bool = False) -> bool:
    a, b = normalize_answer(got, decimal_comma), normalize_answer(expected, decimal_comma)
    fa, fb = _as_fraction(a), _as_fraction(b)
    if fa is not None and fb is not None:
        return fa == fb
    return a == b


def normalize_answer(answer: str, decimal_comma: bool = False) -> str:
    """Reduce an answer to a canonical string. A comma between digits is a decimal comma in most
    languages ("2,5" = 2.5) and a thousands separator in English ("1,000" = 1000)."""
    s = answer.split(r"\approx")[0].replace("{,}", ",")
    s = re.sub(r"\\(left|right|displaystyle)(?![a-zA-Z])|\\[,;! ]|\\?\$", "", s)
    s = re.sub(r"\\[dt]frac", r"\\frac", s)
    s = re.sub(r"\\frac(\d)(\d)", r"\\frac{\1}{\2}", s)  # \frac12 -> \frac{1}{2}
    s = re.sub(r"\\sqrt(\d)", r"\\sqrt{\1}", s)  # \sqrt2 -> \sqrt{2}
    s = re.sub(r"\s+", "", s).rstrip(".")
    s = s.rsplit("=", 1)[-1]  # "f'(1)=1" -> "1"
    s = re.sub(r"\\(text|mathrm)\{[^}]*\}$", "", s)  # trailing unit: "200\text{zł}" -> "200"
    if decimal_comma:
        return re.sub(r"(?<=\d),(?=\d)", ".", s)
    return re.sub(r"(?<=\d),(?=\d{3}(\D|$))", "", s)


def _as_fraction(s: str) -> Fraction | None:
    s = re.sub(r"^(-?)\\frac\{(-?\d+)\}\{(\d+)\}$", r"\1\2/\3", s)
    try:
        return Fraction(s)
    except ValueError, ZeroDivisionError:
        return None


def check_length(output: str, min_words: int | None, max_words: int | None) -> CheckResult:
    words = len(re.findall(r"\w+", output))
    ok = (min_words is None or words >= min_words) and (max_words is None or words <= max_words)
    return CheckResult(name="length", passed=ok, detail=f"{words} words")
