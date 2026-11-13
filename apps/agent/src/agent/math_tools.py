"""The math tools: a model's expression is checked, then sympy works it out in a process of its own.

The expression comes from a model that reads a student's text, and sympy parses it with eval. So
the string is first checked against an allow-list of Python syntax, and the work runs in a child
process (agent.math_worker) that is killed after TIMEOUT_SECONDS: sympy cannot be interrupted from
inside, and some inputs, such as a huge power, never finish. A refusal, a timeout or a sympy error
is returned as a result with an error, for the model to read and try again.
"""

import ast
import asyncio
import re
import sys
from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationError

MAX_EXPRESSION_LENGTH = 500
MAX_NAME_LENGTH = 30
MAX_NUMBER_DIGITS = 50
TIMEOUT_SECONDS = 20  # includes starting the process and importing sympy, about a second
MAX_PROCESSES = 1  # at once: each may use up to 400 MiB, and the sandbox has 512 MiB in all

# The functions an expression may call, by the name sympy knows them as.
FUNCTIONS = frozenset(
    {
        "sin",
        "cos",
        "tan",
        "cot",
        "sec",
        "csc",
        "asin",
        "acos",
        "atan",
        "atan2",
        "sinh",
        "cosh",
        "tanh",
        "asinh",
        "acosh",
        "atanh",
        "exp",
        "log",
        "sqrt",
        "root",
        "Abs",
        "sign",
        "floor",
        "ceiling",
        "factorial",
        "binomial",
        "gcd",
        "lcm",
        "Rational",
        "Min",
        "Max",
    }
)
CONSTANTS = frozenset({"pi", "E", "I", "oo"})  # E is Euler's number: `e` is a plain symbol

_OPERATORS = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.FloorDiv,
    ast.UAdd,
    ast.USub,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)
_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.Call, ast.Name, ast.Load)
_ALLOWED = (*_NODES, *_OPERATORS)
_EQUALS = re.compile(r"(?<![<>=!])==?(?!=)")  # = or ==, not part of <=, >= or !=
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

Variable = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,29}$")]


class Refused(ValueError):
    """The expression is not plain math, or too big."""


def check_expression(expression: str) -> str:
    """Return the expression as Python source sympy may parse, or raise Refused.

    Only numbers, names, arithmetic and comparison, and calls of FUNCTIONS pass; == and != do not.
    `^` means a power, as students write it.
    No attribute, subscript, lambda, string, keyword argument or dunder.
    """
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise Refused(f"The expression is longer than {MAX_EXPRESSION_LENGTH} characters.")
    source = expression.strip().replace("^", "**")
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
        raise Refused(f"Not a valid expression: {type(error).__name__}.") from error
    for node in ast.walk(tree):
        _check_node(node)
    return source


def equation_sides(equation: str) -> list[str]:
    """The sides of `lhs = rhs`, or the text alone when it has no `=`: that means it equals 0."""
    sides = _EQUALS.split(equation)
    if len(sides) > 2:
        raise Refused("Write one equation with a single =.")
    return sides


def _check_node(node: ast.AST) -> None:
    match node:
        case ast.Constant(value=int() | float() as number) if not isinstance(number, bool):
            if len(str(number)) > MAX_NUMBER_DIGITS:
                raise Refused(f"A number has more than {MAX_NUMBER_DIGITS} digits.")
        case ast.Name(id=name):
            if len(name) > MAX_NAME_LENGTH or not _NAME.fullmatch(name):
                raise Refused(f"The name {name[:MAX_NAME_LENGTH]!r} is not allowed.")
        case ast.Call(func=ast.Name(id=name), keywords=[]) if name in FUNCTIONS:
            pass
        case ast.Call():
            raise Refused(
                "Only these functions may be called, with positional arguments: "
                + ", ".join(sorted(FUNCTIONS))
                + "."
            )
        case ast.Eq() | ast.NotEq():
            raise Refused("Use = for an equation, in solve only. Use <, <=, > or >= to compare.")
        case _ if isinstance(node, _ALLOWED):
            pass
        case _:
            raise Refused(f"{type(node).__name__} is not allowed in an expression.")


class MathRequest(BaseModel):
    """What the child process is asked: one operation on one expression."""

    operation: Literal["evaluate", "simplify", "solve", "differentiate", "integrate"]
    expression: str  # for solve, an equation with one `=`, or an expression that equals 0
    variable: Variable = "x"
    lower: str | None = None  # integrate only: the limits of a definite integral
    upper: str | None = None


class MathResult(BaseModel):
    """What a math tool returns to the model. Either `error` is set, or `result` is."""

    result: str | None = None  # the exact result in plain text
    latex: str | None = None  # the same, as LaTeX for the answer
    approximation: str | None = None  # decimal value of a number, 15 digits
    error: str | None = None


_slots = asyncio.Semaphore(MAX_PROCESSES)


async def run_math(request: MathRequest) -> MathResult:
    """Check the request, then run it in a child process that is killed on a timeout."""
    try:
        sides = (
            equation_sides(request.expression)
            if request.operation == "solve"
            else [request.expression]
        )
        for text in (*sides, request.lower, request.upper):
            if text is not None:
                check_expression(text)
    except Refused as refusal:
        return MathResult(error=str(refusal))
    async with _slots:
        return await _run_in_child(request)


async def _run_in_child(request: MathRequest) -> MathResult:
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-m",
        "agent.math_worker",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={},  # nothing of ours for the child to read
    )
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            stdout, _ = await child.communicate(request.model_dump_json().encode())
    except TimeoutError:
        return MathResult(
            error=f"Gave up after {TIMEOUT_SECONDS} seconds: the expression is too hard."
        )
    finally:
        if child.returncode is None:  # a timeout, or the run was cancelled
            child.kill()
            await child.wait()
    try:
        return MathResult.model_validate_json(stdout)
    except ValidationError:
        return MathResult(error="The calculation failed (it ran out of memory or time).")


async def evaluate(expression: str) -> MathResult:
    """Work out the exact value of an expression, such as `sqrt(8) + 2*3` or `binomial(5, 2)`.

    Write `^` or `**` for powers, `pi` for the number pi, `E` for Euler's number, `oo` for infinity.
    """
    return await run_math(MathRequest(operation="evaluate", expression=expression))


async def simplify(expression: str) -> MathResult:
    """Simplify an expression, such as `(x**2 - 1)/(x - 1)`."""
    return await run_math(MathRequest(operation="simplify", expression=expression))


async def solve(equation: str, variable: str = "x") -> MathResult:
    """Solve an equation for a variable, such as `x**2 - 5*x + 6 = 0`. Returns the solutions."""
    return await _with_variable("solve", equation, variable)


async def differentiate(expression: str, variable: str = "x") -> MathResult:
    """Differentiate an expression with respect to a variable."""
    return await _with_variable("differentiate", expression, variable)


async def integrate(
    expression: str, variable: str = "x", lower: str | None = None, upper: str | None = None
) -> MathResult:
    """Integrate an expression with respect to a variable.

    Give lower and upper (numbers or expressions such as `0` and `pi`) for a definite integral,
    or neither for an antiderivative, which has no constant of integration.
    """
    return await _with_variable("integrate", expression, variable, lower, upper)


async def _with_variable(
    operation: Literal["solve", "differentiate", "integrate"],
    expression: str,
    variable: str,
    lower: str | None = None,
    upper: str | None = None,
) -> MathResult:
    try:
        request = MathRequest(
            operation=operation, expression=expression, variable=variable, lower=lower, upper=upper
        )
    except ValidationError:
        return MathResult(error="The variable must be a name such as x or t.")
    return await run_math(request)
