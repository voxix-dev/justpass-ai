"""The child process of the math tools: reads one MathRequest on stdin, writes one MathResult.

Run as `python -m agent.math_worker`. The parent kills it on a timeout. It also caps its own
memory and CPU time, so a runaway power cannot take the machine down before the timeout.
"""

# sympy ships no type stubs
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

import resource
import sys
from typing import Any

import sympy
from sympy.parsing.sympy_parser import auto_number, auto_symbol, parse_expr

from agent.math_tools import (
    CONSTANTS,
    FUNCTIONS,
    MathRequest,
    MathResult,
    check_expression,
    equation_sides,
)

MEMORY_BYTES = 400 << 20  # address space; python with sympy needs about 320 MiB of it
CPU_SECONDS = 30
MAX_TEXT = 4000  # a longer result is cut, so a huge expansion does not flood the model

# What an expression's names mean: the allowed functions and constants, nothing else. The numbers
# and symbols that parse_expr makes of the rest need these four.
NAMESPACE: dict[str, Any] = {
    name: getattr(sympy, name) for name in FUNCTIONS | CONSTANTS | {"Integer", "Float", "Symbol"}
}
NAMESPACE["Abs"] = sympy.Abs


def parse(text: str) -> sympy.Expr:
    """Parse text that check_expression passed. Nothing but NAMESPACE is in reach of eval."""
    source = check_expression(text)
    return parse_expr(
        source,
        local_dict={},
        global_dict={"__builtins__": {}, **NAMESPACE},
        transformations=(auto_symbol, auto_number),
        evaluate=True,
    )


def cut(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "... (cut)"


def solve_equation(equation: str, variable: sympy.Symbol) -> Any:
    left, *right = equation_sides(equation)
    expr = parse(left) - parse(right[0]) if right else parse(left)
    return sympy.solve(expr, variable)


def compute(request: MathRequest) -> Any:
    variable = sympy.Symbol(request.variable)
    match request.operation:
        case "evaluate":
            return parse(request.expression).doit()
        case "simplify":
            return sympy.simplify(parse(request.expression))
        case "solve":
            return solve_equation(request.expression, variable)
        case "differentiate":
            return sympy.diff(parse(request.expression), variable)
        case "integrate":
            expr = parse(request.expression)
            if request.lower is None and request.upper is None:
                return sympy.integrate(expr, variable)
            if request.lower is None or request.upper is None:
                raise ValueError("Give both limits of a definite integral, or neither.")
            return sympy.integrate(expr, (variable, parse(request.lower), parse(request.upper)))


def answer(request: MathRequest) -> MathResult:
    try:
        value = compute(request)
        approximation = None
        if getattr(value, "is_number", False) and not getattr(value, "is_Integer", False):
            approximation = str(sympy.N(value, 15))
        return MathResult(
            result=cut(sympy.sstr(value)),
            latex=cut(sympy.latex(value)),
            approximation=approximation,
        )
    except Exception as error:
        return MathResult(error=cut(f"{type(error).__name__}: {error}"))


def main() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    request = MathRequest.model_validate_json(sys.stdin.buffer.read())
    sys.stdout.write(answer(request).model_dump_json())


if __name__ == "__main__":
    main()
