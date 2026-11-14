"""The math role: it calculates and checks with the math tools, and returns results with steps."""

from pydantic_ai import Agent

from agent import math_tools
from agent.prompt import FORMAT_RULES
from agent.roles.general import Deps
from agent.web_search import WEB_SEARCH, WEB_SEARCH_RULE

SYSTEM_PROMPT = (
    "You do the calculations of a student's homework.\n\n"
    + FORMAT_RULES
    + """- Use the math tools to calculate, simplify, solve, differentiate and integrate, and to
  check your work, instead of doing it in your head.
- Return the results with short steps.
"""
    + WEB_SEARCH_RULE
)

agent = Agent(
    deps_type=Deps,
    instructions=SYSTEM_PROMPT,
    tools=[
        math_tools.evaluate,
        math_tools.simplify,
        math_tools.solve,
        math_tools.differentiate,
        math_tools.integrate,
    ],
    capabilities=[WEB_SEARCH],
    retries=2,
)
