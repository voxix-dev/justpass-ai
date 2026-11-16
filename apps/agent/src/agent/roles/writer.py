"""The writer role: it writes prose, such as essays, explanations and rewrites of an answer."""

from pydantic_ai import Agent

from agent.prompt import FORMAT_RULES, PART_RULE
from agent.roles.general import Deps
from agent.web_search import WEB_SEARCH, WEB_SEARCH_RULE

SYSTEM_PROMPT = (
    "You write the prose of a student's homework: essays, explanations, and rewrites of an "
    "earlier answer given as material.\n\n" + FORMAT_RULES + PART_RULE + WEB_SEARCH_RULE
)

agent = Agent(deps_type=Deps, instructions=SYSTEM_PROMPT, capabilities=[WEB_SEARCH], retries=2)
