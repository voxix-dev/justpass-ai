"""The round: the general role answers, then the curator role saves the notes on the student.

A round with a key runs here, on one model on OpenRouter. The general role gets the task, the
student's profile, the notes on the student in the job's subject and any feedback, and its text
reply is the answer. The curator then saves what the round shows about the student. The
conversation of each job is kept in memory, so a feedback round continues it: the actor's memory
snapshot keeps this process between rounds. A process that has lost it (a fresh actor) rebuilds the
job from the task and its feedback.
"""

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID

import httpx2
from pydantic import SecretStr
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

from agent.context import ContextClient
from agent.formatting import normalize_markdown
from agent.prompt import feedback_message, round_feedback, task_message
from agent.roles.curator import curate, pick_subject
from agent.roles.general import Deps, agent
from shared.agent_run import AgentAnswer, AgentRun
from shared.context import Memory, Profile

logger = logging.getLogger(__name__)

MODEL_ID = "deepseek/deepseek-v4.1-flash"
ROUTING: OpenRouterProviderConfig = {"sort": "price"}  # the cheapest provider of the model
REASONING: OpenRouterReasoning = {"max_tokens": 4_000}  # bounds the thinking before the answer
MAX_OUTPUT_TOKENS = 8_000  # reasoning and answer together, so above the reasoning budget
MODEL_TIMEOUT_SECONDS = 60  # one request to the model
ROUND_TIMEOUT_SECONDS = 180  # a whole round, all its requests
CURATOR_TIMEOUT_SECONDS = 60  # the notes step after the answer
SUBJECT_TIMEOUT_SECONDS = 20  # naming the subject of a job
MAX_MODEL_REQUESTS = 25  # one round: a model that loops on its tools is stopped here

MAX_JOBS = 64  # conversations remembered; the oldest go first


@dataclass
class Checkpoint:
    """A conversation up to the answer of a round, and the rounds whose feedback it holds."""

    rounds: tuple[int, ...]
    messages: list[ModelMessage]


# job id -> round -> the checkpoint after the answer of that round
histories: OrderedDict[UUID, dict[int, Checkpoint]] = OrderedDict()
# job id -> the subject the curator named for it, kept for the later rounds
job_subjects: OrderedDict[UUID, str] = OrderedDict()
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
    async with make_model(body.openrouter_key) as model:  # closes the HTTP client of the round
        if body.context is None:
            async with asyncio.timeout(ROUND_TIMEOUT_SECONDS):
                feedback = [body.feedback] if body.feedback else []
                return await _answer(body, model, body.text, None, None, feedback, None, None)
        async with ContextClient(body.context.url, body.context.token) as context:
            async with asyncio.timeout(ROUND_TIMEOUT_SECONDS):
                task = await context.get_task()
                profile = await context.get_profile()
                subject = await _job_subject(body, model, task.text, profile)
                profile = profile.model_copy(update={"subject": subject})
                subject_memory = await context.get_subject_memory(subject)
                feedback = round_feedback(task, body.round)
                answered = tuple(
                    r.number
                    for r in task.rounds
                    if r.outcome == "answered" and r.number < body.round
                )
                answer = await _answer(
                    body, model, task.text, profile, subject_memory, feedback, context, answered
                )
            # Only the newest run of the job writes notes. A failed curator never fails the round.
            if newest_runs.get(body.job_id) == (body.round, body.lease_id):
                try:
                    async with asyncio.timeout(CURATOR_TIMEOUT_SECONDS):
                        await curate(
                            model,
                            context,
                            task_text=task.text,
                            subject=profile.subject,
                            round_number=body.round,
                            feedback=body.feedback,
                            answer=answer.answer,
                            profile=profile,
                            subject_memory=subject_memory,
                        )
                except Exception as error:
                    logger.warning(
                        "[curator] Round %s of job %s: the notes were not saved (%s)",
                        body.round,
                        body.job_id,
                        type(error).__name__,
                    )
            return answer


async def _job_subject(body: AgentRun, model: Model, task_text: str, profile: Profile) -> str:
    """The subject of the job: the one kept from an earlier round, else the one the curator names.
    A curator that fails leaves the subject the API gave the job."""
    kept = job_subjects.get(body.job_id)
    if kept is not None:
        job_subjects.move_to_end(body.job_id)
        return kept
    try:
        async with asyncio.timeout(SUBJECT_TIMEOUT_SECONDS):
            subject = await pick_subject(model, task_text, profile.subjects)
    except Exception as error:
        logger.warning(
            "[curator] Round %s of job %s: no subject named (%s)",
            body.round,
            body.job_id,
            type(error).__name__,
        )
        return profile.subject
    if newest_runs.get(body.job_id) == (body.round, body.lease_id):
        job_subjects[body.job_id] = subject
        while len(job_subjects) > MAX_JOBS:
            job_subjects.popitem(last=False)
    return subject


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
    subject_memory: Memory | None,
    feedback: list[str],
    context: ContextClient | None,
    answered: tuple[int, ...] | None,
) -> AgentAnswer:
    this_run = (body.round, body.lease_id)
    checkpoints = histories.get(body.job_id, {})
    candidates = [
        number
        for number, checkpoint in checkpoints.items()
        if number < body.round and (answered is None or checkpoint.rounds == answered)
    ]
    # A feedback round continues the newest earlier checkpoint, so a round that runs again does not
    # start from its own first attempt. With a Context API, only a checkpoint that holds exactly the
    # answered rounds qualifies: it neither misses the feedback of an answered round nor brings
    # back that of a failed one. Otherwise the round rebuilds from the task.
    if candidates and body.feedback:
        chosen = max(candidates)
        prompt = feedback_message(
            body.feedback, profile, subject_memory.content_md if subject_memory else ""
        )
        history = checkpoints[chosen].messages
        rounds = (*checkpoints[chosen].rounds, body.round)
        logger.info(
            "[general] Round %s of job %s continues round %s", body.round, body.job_id, chosen
        )
    else:
        prompt, history = (
            task_message(
                text, profile, feedback, subject_memory.content_md if subject_memory else ""
            ),
            None,
        )
        rounds = (*(answered or ()), body.round)
        logger.info(
            "[general] Round %s of job %s starts from the task and %d feedback",
            body.round,
            body.job_id,
            len(feedback),
        )
    result = await agent.run(
        prompt,
        model=model,
        deps=Deps(context, profile.subject if profile else None),
        message_history=history,
        usage_limits=UsageLimits(request_limit=MAX_MODEL_REQUESTS),
    )
    _log_run(body, result.new_messages(), result.usage)
    # A run that a newer lease or round has overtaken must not replace its conversation.
    if newest_runs.get(body.job_id) == this_run:
        kept = histories.setdefault(body.job_id, {})
        for number in [n for n in kept if n > body.round]:
            del kept[number]
        kept[body.round] = Checkpoint(rounds, result.all_messages())
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
        "[general] Round %s of job %s: %d model requests, tools %s, provider %s, "
        "tokens in %d out %d",
        body.round, body.job_id, len(responses), tools, providers,
        usage.input_tokens, usage.output_tokens,
    )  # fmt: skip
