"""The agent: one model on OpenRouter, with math tools and a tool that saves a document.

A round with a key runs here. The model gets the task, the student's profile and any feedback, and
its text reply is the answer. The conversation of each job is kept in memory, so a feedback round
continues it: the actor's memory snapshot keeps this process between rounds. A process that has
lost it (a fresh actor) rebuilds the job from the task and its feedback.
"""

import logging
from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID

import httpx2
from pydantic import SecretStr, ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext
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
from agent.context import ContextClient
from agent.prompt import (
    SYSTEM_PROMPT,
    feedback_message,
    round_feedback,
    task_message,
)
from shared.agent_run import AgentAnswer, AgentRun
from shared.context import Profile

logger = logging.getLogger(__name__)

MODEL_ID = "deepseek/deepseek-v4.1-flash"
ROUTING: OpenRouterProviderConfig = {"sort": "price"}  # the cheapest provider of the model
MAX_OUTPUT_TOKENS = 16_000
MODEL_TIMEOUT_SECONDS = 120  # one request to the model
MAX_MODEL_REQUESTS = 25  # one round: a model that loops on its tools is stopped here

MAX_JOBS = 64  # conversations remembered; the oldest go first

DOCUMENTS_UNAVAILABLE = "Documents are unavailable in this round. Put everything in your answer."


@dataclass
class Deps:
    """What the tools reach: the Context API of this round, or None when there is none."""

    context: ContextClient | None


async def write_document(ctx: RunContext[Deps], title: str, content_md: str) -> str:
    """Save a document for the student: a Markdown text with math in KaTeX, and a short title.

    It is kept next to the answer, which is your reply and not a document.
    """
    if ctx.deps.context is None:
        return DOCUMENTS_UNAVAILABLE
    try:
        await ctx.deps.context.write_draft(title, content_md)
    except ValidationError as error:  # too long, or a NUL character
        reasons = "; ".join(f"{e['loc'][-1]}: {e['msg']}" for e in error.errors())
        raise ModelRetry(f"The document was refused ({reasons}). Fix it and call again.") from error
    return f"Saved the document {title!r}."


agent = Agent(
    deps_type=Deps,
    instructions=SYSTEM_PROMPT,
    tools=[
        math_tools.evaluate,
        math_tools.simplify,
        math_tools.solve,
        math_tools.differentiate,
        math_tools.integrate,
        write_document,
    ],
    retries=2,
)

# job id -> the last round that was answered, and the conversation up to its answer
histories: OrderedDict[UUID, tuple[int, list[ModelMessage]]] = OrderedDict()


def make_model(key: SecretStr, http_client: httpx2.AsyncClient | None = None) -> Model:
    provider = OpenRouterProvider(api_key=key.get_secret_value(), http_client=http_client)
    settings = OpenRouterModelSettings(
        openrouter_provider=ROUTING,
        max_tokens=MAX_OUTPUT_TOKENS,
        timeout=MODEL_TIMEOUT_SECONDS,
    )
    return OpenRouterModel(MODEL_ID, provider=provider, settings=settings)


async def solve_with_model(body: AgentRun) -> AgentAnswer:
    """Run the round on the model. A Context API call that fails, or a model that fails, raises."""
    assert body.openrouter_key is not None
    model = make_model(body.openrouter_key)
    if body.context is None:
        feedback = [body.feedback] if body.feedback else []
        return await _answer(body, model, body.text, None, feedback, None)
    async with ContextClient(body.context.url, body.context.token) as context:
        task = await context.get_task()
        profile = await context.get_profile()
        feedback = round_feedback(task, body.round)
        return await _answer(body, model, task.text, profile, feedback, context)


async def _answer(
    body: AgentRun,
    model: Model,
    text: str,
    profile: Profile | None,
    feedback: list[str],
    context: ContextClient | None,
) -> AgentAnswer:
    past = histories.get(body.job_id)
    # The conversation is used for a later round only: a round that runs again starts from scratch.
    if past is not None and past[0] < body.round and body.feedback:
        prompt, history = feedback_message(body.feedback), past[1]
    else:
        prompt, history = task_message(text, profile, feedback), None
    result = await agent.run(
        prompt,
        model=model,
        deps=Deps(context),
        message_history=history,
        usage_limits=UsageLimits(request_limit=MAX_MODEL_REQUESTS),
    )
    _log_run(body, result.new_messages(), result.usage)
    histories[body.job_id] = (body.round, result.all_messages())
    histories.move_to_end(body.job_id)
    while len(histories) > MAX_JOBS:
        histories.popitem(last=False)
    answer = result.output.replace("\x00", "").strip()  # an answer cannot hold NUL
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
