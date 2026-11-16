"""The round: the orchestrator plans and the workers work, then the curator role saves the notes.

A round with a key runs here, on OpenRouter. The orchestrator gets the task, the student's profile,
the notes on the student in the job's subject and any feedback, and delegates the work to the
workers; the results it lists are the answer. The curator then saves what the round shows about the
student. The orchestrator's conversation and the results of each job are kept in memory, so a
feedback round continues them: the actor's memory snapshot keeps this process between rounds. A
process that has lost it (a fresh actor) rebuilds the job from the task and its feedback. An
orchestrator that fails does not fail the round: a worker's result answers instead.
"""

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID

import httpx2
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from agent.context import ContextClient, ContextError
from agent.files import task_text_with_files
from agent.models import MODEL_TIMEOUT_SECONDS, Role, make_models, reported_cost
from agent.prompt import feedback_message, round_feedback, task_message
from agent.roles.curator import curate, pick_subject
from agent.roles.orchestrator import (
    Job,
    Result,
    RoundState,
    answer_of,
    fallback_result,
    orchestrate,
    run_worker,
)
from shared.agent_run import AgentAnswer, AgentRun
from shared.context import Memory, Profile

logger = logging.getLogger(__name__)

ROUND_TIMEOUT_SECONDS = 300  # a whole round, the orchestrator and the workers together
ROUND_GRACE_SECONDS = 1  # after the orchestrator's deadline, to answer with a result in hand
CURATOR_TIMEOUT_SECONDS = 60  # the notes step after the answer
SUBJECT_TIMEOUT_SECONDS = 20  # naming the subject of a job

MAX_JOBS = 64  # conversations remembered; the oldest go first


@dataclass
class Checkpoint:
    """The orchestrator's conversation up to the answer of a round, the results of the job so far
    and the rounds whose feedback the conversation holds."""

    rounds: tuple[int, ...]
    messages: list[ModelMessage]
    results: dict[str, Result]


@dataclass
class Spend:
    """What the round used, all roles together."""

    tokens: int = 0
    cost: float = 0  # USD as OpenRouter reports it


class Metered(WrapperModel):
    """A model that adds up the tokens and the reported cost of what it answers."""

    def __init__(self, wrapped: Model) -> None:
        super().__init__(wrapped)
        self.spend = Spend()

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        response = await super().request(messages, model_settings, model_request_parameters)
        self.spend.tokens += response.usage.total_tokens
        self.spend.cost += reported_cost([response])
        return response


# job id -> round -> the checkpoint after the answer of that round
histories: OrderedDict[UUID, dict[int, Checkpoint]] = OrderedDict()
# job id -> the subject the curator named for it, kept for the later rounds
job_subjects: OrderedDict[UUID, str] = OrderedDict()
# job id -> round and lease of the newest run started: only that run publishes its history
newest_runs: OrderedDict[UUID, tuple[int, UUID | None]] = OrderedDict()


async def solve_with_model(body: AgentRun) -> AgentAnswer:
    """Run the round on the models. A Context API call that fails, a round that overruns, or an
    orchestrator and a fallback worker that both fail, raises."""
    assert body.openrouter_key is not None
    # Register before the first await, so that the order of the leases is the order they started
    # in. A newer run that fails keeps the ownership: the API retries under a still newer lease,
    # and the writes of the old lease are refused anyway.
    _register_run(body)
    deadline = asyncio.get_running_loop().time() + ROUND_TIMEOUT_SECONDS
    # One HTTP client for all the roles of the round, closed with it.
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(MODEL_TIMEOUT_SECONDS, connect=5)) as http:
        # One metered model per role, so the round's total counts every response, failed runs too.
        metered: dict[Role, Metered] = {
            role: Metered(model) for role, model in make_models(body.openrouter_key, http).items()
        }
        models: dict[Role, Model] = dict(metered)
        curator_model = metered["curator"]
        if body.context is None:
            async with asyncio.timeout_at(deadline + ROUND_GRACE_SECONDS):
                feedback = [body.feedback] if body.feedback else []
                answer = await _answer(
                    body, models, body.text, None, None, feedback, None, None, deadline
                )
        else:
            async with ContextClient(body.context.url, body.context.token) as context:
                async with asyncio.timeout_at(deadline + ROUND_GRACE_SECONDS):
                    task = await context.get_task()
                    # Files first: the roles read the task with their text in it.
                    task_text = await task_text_with_files(
                        task,
                        context,
                        metered["reader"],
                        lambda: newest_runs.get(body.job_id) == (body.round, body.lease_id),
                    )
                    profile = await context.get_profile()
                    subject = await _job_subject(body, curator_model, task_text, profile)
                    profile = profile.model_copy(update={"subject": subject})
                    subject_memory = await context.get_subject_memory(subject)
                    feedback = round_feedback(task, body.round)
                    answered = tuple(
                        r.number
                        for r in task.rounds
                        if r.outcome == "answered" and r.number < body.round
                    )
                    answer = await _answer(
                        body, models, task_text, profile, subject_memory, feedback, context,
                        answered, deadline,
                    )  # fmt: skip
                # Only the newest run of the job writes notes. A failed curator never fails the
                # round.
                if newest_runs.get(body.job_id) == (body.round, body.lease_id):
                    try:
                        async with asyncio.timeout(CURATOR_TIMEOUT_SECONDS):
                            await curate(
                                curator_model,
                                context,
                                task_text=task_text,
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
    tokens = sum(model.spend.tokens for model in metered.values())
    cost = sum(model.spend.cost for model in metered.values())
    logger.info("[round] %s: tokens %d (all roles), cost $%.4f", _prefix(body), tokens, cost)
    return answer


def _prefix(body: AgentRun) -> str:
    return f"Round {body.round} of job {body.job_id}"


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
    models: dict[Role, Model],
    text: str,
    profile: Profile | None,
    subject_memory: Memory | None,
    feedback: list[str],
    context: ContextClient | None,
    answered: tuple[int, ...] | None,
    deadline: float,
) -> AgentAnswer:
    this_run = (body.round, body.lease_id)
    prefix = _prefix(body)
    notes = subject_memory.content_md if subject_memory else ""
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
        prompt = feedback_message(body.feedback, profile, notes)
        history = checkpoints[chosen].messages
        results = dict(checkpoints[chosen].results)
        rounds = (*checkpoints[chosen].rounds, body.round)
        logger.info("[orchestrator] %s continues round %s", prefix, chosen)
    else:
        prompt, history, results = task_message(text, profile, feedback, notes), None, {}
        rounds = (*(answered or ()), body.round)
        logger.info("[orchestrator] %s starts from the task and %d feedback", prefix, len(feedback))
    state = RoundState(
        Job(text, profile, notes, feedback, context, body.round),
        models,
        results,
        deadline,
        log_prefix=prefix,
    )
    failure: Exception | None = None
    try:
        async with asyncio.timeout_at(deadline):
            run = await orchestrate(prompt, history, state)
        answer = answer_of(run.output, state.results)
    except ContextError:
        raise  # a failed Context API call fails the round: the answer could miss what it saved
    except Exception as error:
        # The round's time is up: only a result already done can answer, no new work starts.
        if asyncio.get_running_loop().time() >= deadline and fallback_result(state) is None:
            raise
        answer, failure, run = None, error, None
    if run is not None and answer:
        new_messages = run.new_messages()
        _log_orchestrator(
            state, body, new_messages, run.usage.input_tokens, run.usage.output_tokens
        )
        # A run that a newer lease or round has overtaken must not replace its conversation.
        if newest_runs.get(body.job_id) == this_run:
            kept = histories.setdefault(body.job_id, {})
            for number in [n for n in kept if n > body.round]:
                del kept[number]
            kept[body.round] = Checkpoint(rounds, run.all_messages(), dict(state.results))
            histories.move_to_end(body.job_id)
            while len(histories) > MAX_JOBS:
                histories.popitem(last=False)
    else:
        # A checkpoint of an earlier attempt of this round or a later one holds a conversation the
        # student never saw: the next round must not continue it.
        if newest_runs.get(body.job_id) == this_run:
            kept = histories.get(body.job_id, {})
            for number in [n for n in kept if n >= body.round]:
                del kept[number]
        answer = await _fall_back(state, prefix, failure)
    return AgentAnswer(answer=answer or None)


async def _fall_back(state: RoundState, prefix: str, failure: Exception | None) -> str:
    """Answer with a result the workers already have, or with the general worker on the whole
    task. A fallback round keeps no checkpoint: the orchestrator's conversation is not sound."""
    reason = type(failure).__name__ if failure else "no usable answer"
    fallback = fallback_result(state)
    if fallback is None:
        fallback = await run_worker(state, "general", "Answer the whole task.", [])
    if fallback.status == "done":
        logger.warning(
            "[orchestrator] %s: failed (%s), fell back to %s result %s",
            prefix, reason, fallback.role, fallback.id,
        )  # fmt: skip
        return fallback.text
    if fallback.text == "EmptyAnswer":
        return ""  # the model had nothing to say: no answer, as for any empty reply
    raise RuntimeError(f"The round failed ({reason}) and so did the fallback ({fallback.text})")


def _log_orchestrator(
    state: RoundState, body: AgentRun, messages: list[ModelMessage], tokens_in: int, tokens_out: int
) -> None:
    requests = sum(isinstance(m, ModelResponse) for m in messages)
    calls = [r.role for r in state.results.values() if r.round == body.round]
    logger.info(
        "[orchestrator] %s: %d model requests, calls %s, tokens in %d out %d",
        state.log_prefix, requests, calls, tokens_in, tokens_out,
    )  # fmt: skip
