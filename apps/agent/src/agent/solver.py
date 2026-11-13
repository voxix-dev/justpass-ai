"""The agent: one model on OpenRouter, with math tools.

A round with a key and no feedback runs here. The model works from the task text alone, and its
text reply is the answer. Every round is a fresh conversation. Feedback rounds get the fixed
sample for now (server.py).
"""

import logging

import httpx2
from pydantic import SecretStr
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import (
    OpenRouterModel,
    OpenRouterModelSettings,
    OpenRouterProviderConfig,
)
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import RunUsage, UsageLimits

from agent import math_tools
from agent.formatting import normalize_markdown
from agent.prompt import SYSTEM_PROMPT
from shared.agent_run import AgentAnswer, AgentRun

logger = logging.getLogger(__name__)

MODEL_ID = "deepseek/deepseek-v4.1-flash"
ROUTING: OpenRouterProviderConfig = {"sort": "price"}  # the cheapest provider of the model
MAX_OUTPUT_TOKENS = 16_000
MODEL_TIMEOUT_SECONDS = 120  # one request to the model
MAX_MODEL_REQUESTS = 25  # one round: a model that loops on its tools is stopped here

agent = Agent(
    instructions=SYSTEM_PROMPT,
    tools=[
        math_tools.evaluate,
        math_tools.simplify,
        math_tools.solve,
        math_tools.differentiate,
        math_tools.integrate,
    ],
    retries=2,
)


def make_model(key: SecretStr, http_client: httpx2.AsyncClient | None = None) -> Model:
    provider = OpenRouterProvider(api_key=key.get_secret_value(), http_client=http_client)
    settings = OpenRouterModelSettings(
        openrouter_provider=ROUTING,
        max_tokens=MAX_OUTPUT_TOKENS,
        timeout=MODEL_TIMEOUT_SECONDS,
    )
    return OpenRouterModel(MODEL_ID, provider=provider, settings=settings)


async def solve_with_model(body: AgentRun) -> AgentAnswer:
    """Ask the model to solve the task text. A model that fails raises."""
    assert body.openrouter_key is not None
    async with make_model(body.openrouter_key) as model:  # closes the HTTP client of the round
        result = await agent.run(
            body.text,
            model=model,
            usage_limits=UsageLimits(request_limit=MAX_MODEL_REQUESTS),
        )
    _log_run(body, result.new_messages(), result.usage)
    answer = normalize_markdown(
        result.output.replace("\x00", "")
    ).strip()  # an answer cannot hold NUL
    return AgentAnswer(answer=answer or None)


def _log_run(body: AgentRun, messages: list[ModelMessage], usage: RunUsage) -> None:
    """Say which tools the round called, which provider served it and the tokens, never the key."""
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    tools = [p.tool_name for m in responses for p in m.parts if isinstance(p, ToolCallPart)]
    providers = sorted(
        {
            str(m.provider_details.get("downstream_provider"))
            for m in responses
            if m.provider_details
        }
    )
    logger.info(
        "Round %s of job %s: %d model requests, tools %s, provider %s, tokens in %d out %d",
        body.round, body.job_id, len(responses), tools, providers,
        usage.input_tokens, usage.output_tokens,
    )  # fmt: skip
