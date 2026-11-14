"""The agent: one model on OpenRouter, with math tools and a tool that saves a document.

A round with a key runs here. The model gets the task, the student's profile and any feedback, and
its text reply is the answer. The conversation of each job is kept in memory, so a feedback round
continues it: the actor's memory snapshot keeps this process between rounds. A process that has
lost it (a fresh actor) rebuilds the job from the task and its feedback.
"""

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID

import httpx2
from pydantic import BaseModel, Field, SecretStr, ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import (
    OpenRouterModel,
    OpenRouterModelSettings,
    OpenRouterProviderConfig,
    OpenRouterReasoning,
)
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import RunUsage, UsageLimits

from agent import math_tools
from agent.context import ContextClient
from agent.formatting import normalize_markdown
from agent.prompt import (
    SYSTEM_PROMPT,
    feedback_message,
    round_feedback,
    task_message,
)
from shared.agent_run import AgentAnswer, AgentRun
from shared.context import Profile, Text

logger = logging.getLogger(__name__)

MODEL_ID = "deepseek/deepseek-v4.1-flash"
ROUTING: OpenRouterProviderConfig = {"sort": "price"}  # the cheapest provider of the model
REASONING: OpenRouterReasoning = {"max_tokens": 4_000}  # bounds the thinking before the answer
MAX_OUTPUT_TOKENS = 8_000  # reasoning and answer together, so above the reasoning budget
MODEL_TIMEOUT_SECONDS = 60  # one request to the model
ROUND_TIMEOUT_SECONDS = 180  # a whole round, all its requests
MAX_MODEL_REQUESTS = 25  # one round: a model that loops on its tools is stopped here

MAX_JOBS = 64  # conversations remembered; the oldest go first

DOCUMENTS_UNAVAILABLE = "Documents are unavailable in this round. Put everything in your answer."


@dataclass
class Deps:
    """What the tools reach: the Context API of this round, or None when there is none."""

    context: ContextClient | None


class DocumentResult(BaseModel):
    """What the model is told about a document it asked to save."""

    saved: bool
    message: str


class DocumentInput(BaseModel):
    """The arguments of write_document, with the limits of the Context API."""

    title: Text = Field(max_length=200)
    content_md: Text = Field(max_length=100_000)


async def write_document(ctx: RunContext[Deps], document: DocumentInput) -> DocumentResult:
    """Save a document for the student: a Markdown text with math in KaTeX, and a short title.

    It is kept next to the answer, which is your reply and not a document.
    """
    if ctx.deps.context is None:
        return DocumentResult(saved=False, message=DOCUMENTS_UNAVAILABLE)
    try:
        await ctx.deps.context.write_draft(document.title, document.content_md)
    except ValidationError as error:  # too long, or a NUL character
        reasons = "; ".join(f"{e['loc'][-1]}: {e['msg']}" for e in error.errors())
        raise ModelRetry(f"The document was refused ({reasons}). Fix it and call again.") from error
    return DocumentResult(saved=True, message=f"Saved the document {document.title!r}.")


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

# job id -> round -> the conversation up to the answer of that round
histories: OrderedDict[UUID, dict[int, list[ModelMessage]]] = OrderedDict()
# job id -> round and lease of the newest run started: only that run publishes its history
newest_runs: OrderedDict[UUID, tuple[int, UUID | None]] = OrderedDict()


def make_model(key: SecretStr, http_client: httpx2.AsyncClient | None = None) -> Model:
    provider = OpenRouterProvider(api_key=key.get_secret_value(), http_client=http_client)
    settings = OpenRouterModelSettings(
        openrouter_provider=ROUTING,
        openrouter_reasoning=REASONING,
        max_tokens=MAX_OUTPUT_TOKENS,
        timeout=MODEL_TIMEOUT_SECONDS,
    )
    return OpenRouterModel(MODEL_ID, provider=provider, settings=settings)


async def solve_with_model(body: AgentRun) -> AgentAnswer:
    """Run the round on the model. A Context API call or a model that fails, or a round that
    overruns, raises."""
    assert body.openrouter_key is not None
    # Register before the first await, so that the order of the leases is the order they started
    # in. A newer run that fails keeps the ownership: the API retries under a still newer lease,
    # and the writes of the old lease are refused anyway.
    _register_run(body)
    async with (
        asyncio.timeout(ROUND_TIMEOUT_SECONDS),
        make_model(body.openrouter_key) as model,  # closes the HTTP client of the round
    ):
        if body.context is None:
            feedback = [body.feedback] if body.feedback else []
            return await _answer(body, model, body.text, None, feedback, None)
        async with ContextClient(body.context.url, body.context.token) as context:
            task = await context.get_task()
            profile = await context.get_profile()
            feedback = round_feedback(task, body.round)
            return await _answer(body, model, task.text, profile, feedback, context)


def _register_run(body: AgentRun) -> None:
    newest = newest_runs.get(body.job_id)
    if newest is None or newest[0] <= body.round:
        newest_runs[body.job_id] = (body.round, body.lease_id)
        newest_runs.move_to_end(body.job_id)
        while len(newest_runs) > MAX_JOBS:
            newest_runs.popitem(last=False)


async def _answer(
    body: AgentRun,
    model: Model,
    text: str,
    profile: Profile | None,
    feedback: list[str],
    context: ContextClient | None,
) -> AgentAnswer:
    this_run = (body.round, body.lease_id)
    checkpoints = histories.get(body.job_id, {})
    earlier = [number for number in checkpoints if number < body.round]
    # A feedback round continues the newest earlier round: a round that runs again does not start
    # from its own first attempt, and keeps the feedback of the rounds before it.
    if earlier and body.feedback:
        prompt, history = feedback_message(body.feedback), checkpoints[max(earlier)]
        logger.info("Round %s of job %s continues round %s", body.round, body.job_id, max(earlier))
    else:
        prompt, history = task_message(text, profile, feedback), None
        logger.info(
            "Round %s of job %s starts from the task and %d feedback",
            body.round,
            body.job_id,
            len(feedback),
        )
    result = await agent.run(
        prompt,
        model=model,
        deps=Deps(context),
        message_history=history,
        usage_limits=UsageLimits(request_limit=MAX_MODEL_REQUESTS),
    )
    _log_run(body, result.new_messages(), result.usage)
    # A run that a newer lease or round has overtaken must not replace its conversation.
    if newest_runs.get(body.job_id) == this_run:
        kept = histories.setdefault(body.job_id, {})
        for number in [n for n in kept if n > body.round]:
            del kept[number]
        kept[body.round] = result.all_messages()
        histories.move_to_end(body.job_id)
        while len(histories) > MAX_JOBS:
            histories.popitem(last=False)
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
