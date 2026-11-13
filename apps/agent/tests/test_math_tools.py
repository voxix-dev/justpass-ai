import asyncio
import time

import pytest

from agent import math_tools
from agent.math_tools import (
    MathResult,
    check_expression,
    differentiate,
    evaluate,
    integrate,
    simplify,
    solve,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_evaluate() -> None:
    result = await evaluate("sqrt(8) + 2*3 + 2^3")
    assert result.error is None
    assert result.result == "2*sqrt(2) + 14"
    assert result.approximation is not None
    assert result.approximation.startswith("16.828")
    assert result.latex


async def test_evaluate_integer_has_no_approximation() -> None:
    result = await evaluate("binomial(5, 2) + factorial(4)")
    assert result == MathResult(result="34", latex="34")


async def test_simplify() -> None:
    result = await simplify("(x**2 - 1)/(x - 1)")
    assert result.result == "x + 1"


async def test_solve_equation() -> None:
    result = await solve("x**2 - 5*x + 6 = 0")
    assert result.result == "[2, 3]"


async def test_solve_without_equals_means_zero() -> None:
    result = await solve("t - 4", "t")
    assert result.result == "[4]"


async def test_solve_double_equals_is_an_equation() -> None:
    result = await solve("x == 2")
    assert result.result == "[2]"


async def test_double_equals_and_not_equals_refused() -> None:
    for call, text in ((evaluate, "1 == 1"), (simplify, "x == 2"), (solve, "x != 2")):
        result = await call(text)
        assert result.error and "=" in result.error and result.result is None


async def test_solve_refuses_two_equals() -> None:
    result = await solve("x = 1 = 2")
    assert result.error and result.result is None


async def test_differentiate() -> None:
    result = await differentiate("x**3 + sin(t*x)")
    assert result.result == "t*cos(t*x) + 3*x**2"


async def test_integrate() -> None:
    result = await integrate("2*x")
    assert result.result == "x**2"


async def test_integrate_definite() -> None:
    result = await integrate("sin(x)", "x", "0", "pi")
    assert result.result == "2"


async def test_integrate_one_limit_is_an_error() -> None:
    result = await integrate("x", "x", "0")
    assert result.error and "both limits" in result.error


async def test_sympy_error_is_returned() -> None:
    result = await evaluate("sin(1, 2, 3)")
    assert result.error and result.result is None


async def test_bad_variable_is_an_error() -> None:
    result = await differentiate("x**2", "__class__")
    assert result.error and result.result is None


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "().__class__",
        "x.__dict__",
        "x.real",
        "'abc'",
        "f'{1}'",
        "lambda: 1",
        "[i for i in x]",
        "x[0]",
        "__builtins__",
        "_x",
        "open('/etc/passwd')",
        "exec('1')",
        "sin(x=1)",
        "sin(*x)",
        "(x := 1)",
        "x if x else 1",
        "True",
        "1j",
        "x; y",
        "a" * 600,
        "1" * 60,
        "Symbol('x')",
        "eval('1')",
    ],
)
async def test_refused(expression: str) -> None:
    for call in (evaluate, simplify, solve, differentiate, integrate):
        result = await call(expression)
        assert result.error and result.result is None, (call, expression)


def test_check_expression_accepts_plain_math() -> None:
    assert check_expression(" 2^x + sin(pi/2) <= y ") == "2**x + sin(pi/2) <= y"


async def test_refused_without_starting_a_process(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a process was started")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    result = await evaluate("().__class__")
    assert result.error


@pytest.mark.parametrize("expression", ["9**9**9**9", "factorial(10**12)", "2**(10**12)"])
async def test_runaway_work_returns_an_error_quickly(
    monkeypatch: pytest.MonkeyPatch, expression: str
) -> None:
    monkeypatch.setattr(math_tools, "TIMEOUT_SECONDS", 3)
    start = time.monotonic()
    result = await evaluate(expression)
    assert time.monotonic() - start < 6
    assert result.error and result.result is None


async def test_timeout_kills_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(math_tools, "TIMEOUT_SECONDS", 0.2)
    spawned: list[asyncio.subprocess.Process] = []
    real = asyncio.create_subprocess_exec

    async def spy(*args: str, **kwargs: object) -> asyncio.subprocess.Process:
        child = await real(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        spawned.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    result = await evaluate("1 + 1")
    assert result.error and "Gave up" in result.error
    assert spawned and spawned[0].returncode is not None
