"""The agent: one model on OpenRouter, with math tools, a document tool and memory tools.

A round with a key runs here. The model gets the task, the student's profile, the notes on the
student in the job's subject and any feedback, and its text reply is the answer. It saves and
searches notes on the student with the memory tools. The conversation of each job is kept in
memory, so a feedback round continues it: the actor's memory snapshot keeps this process between
rounds. A process that has lost it (a fresh actor) rebuilds the job from the task and its feedback.
"""

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

import httpx2
from pydantic import BaseModel, Field, SecretStr, StringConstraints, ValidationError
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
    fence,
    round_feedback,
    task_message,
)
from shared.agent_run import AgentAnswer, AgentRun
from shared.context import (
    GetSubjectMemory,
    HistoryItem,
    ListHistory,
    Memory,
    Profile,
    SearchHistory,
    Text,
)

logger = logging.getLogger(__name__)

MODEL_ID = "deepseek/deepseek-v4.1-flash"
ROUTING: OpenRouterProviderConfig = {"sort": "price"}  # the cheapest provider of the model
REASONING: OpenRouterReasoning = {"max_tokens": 4_000}  # bounds the thinking before the answer
MAX_OUTPUT_TOKENS = 8_000  # reasoning and answer together, so above the reasoning budget
MODEL_TIMEOUT_SECONDS = 60  # one request to the model
ROUND_TIMEOUT_SECONDS = 180  # a whole round, all its requests
MAX_SAVE_ATTEMPTS = 3  # a notes save that finds the doc changed meanwhile applies again
MAX_MODEL_REQUESTS = 25  # one round: a model that loops on its tools is stopped here

MAX_JOBS = 64  # conversations remembered; the oldest go first

DOCUMENTS_UNAVAILABLE = "Documents are unavailable in this round. Put everything in your answer."
MEMORY_UNAVAILABLE = "Memory is unavailable in this round."


@dataclass
class Deps:
    """What the tools reach: the Context API of this round, or None when there is none.

    versions and notes hold the version and the text of each notes doc as last known, which a save
    is based on. subject is the job's subject, so a read of it refreshes them.
    """

    context: ContextClient | None
    versions: dict[Literal["profile", "subject"], int] = field(
        default_factory=lambda: dict[Literal["profile", "subject"], int]()
    )
    notes: dict[Literal["profile", "subject"], str] = field(
        default_factory=lambda: dict[Literal["profile", "subject"], str]()
    )
    subject: str | None = None


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


class MemoryResult(BaseModel):
    """What the model is told about notes it asked to save."""

    saved: bool
    message: str


Note = Annotated[str, StringConstraints(max_length=500)]  # a NUL is refused by the save


class NotesInput(BaseModel):
    """Notes to add to or remove from the profile doc, or the subject doc of this job."""

    scope: Literal["profile", "subject"] = Field(
        description=(
            "profile: the student in every subject (how they want answers: length, format; how they"
            " write). subject: only this job's subject (its teacher's rules, mistakes they repeat"
            " in it)."
        )
    )
    add: list[Note] = Field(
        default_factory=list,
        max_length=20,
        description="New lasting facts, one per item, each a short sentence.",
    )
    remove: list[Note] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Earlier notes to drop because they are wrong or replaced, copied exactly as shown."
        ),
    )


def _note_text(line: str) -> str:
    return line[2:].strip() if line.startswith("- ") else line.strip()


def _apply_notes(body: str, add: list[str], remove: list[str]) -> tuple[str, list[str]]:
    """Apply the changes to a doc of bullet lines: the new doc, and the removals that match none."""
    dropped = {item.strip().casefold() for item in remove}
    lines = [line.rstrip() for line in body.splitlines() if line.strip()]
    present = {_note_text(line).casefold() for line in lines}
    unmatched = [item for item in remove if item.strip().casefold() not in present]
    kept = [line for line in lines if _note_text(line).casefold() not in dropped]
    seen = {_note_text(line).casefold() for line in kept}
    for item in add:
        text = " ".join(item.split())
        if text and text.casefold() not in seen:
            kept.append(f"- {text}")
            seen.add(text.casefold())
    return "\n".join(kept), unmatched


def _shown(body: str) -> str:
    return fence(body) if body else "(none)"


async def _refresh_notes(deps: Deps, context: ContextClient, scope: Literal["profile", "subject"]):
    if scope == "profile":
        current = await context.get_profile()
        deps.versions["profile"] = current.profile_version
        deps.notes["profile"] = current.profile_md
    else:
        memory = await context.get_subject_memory()
        deps.versions["subject"] = memory.version
        deps.notes["subject"] = memory.content_md


async def update_memory(ctx: RunContext[Deps], notes: NotesInput) -> MemoryResult:
    """Add and remove lasting notes on the student: scope "profile", or "subject" (this job's).

    Give only the facts to add and the earlier notes to remove (read_memory shows them); the rest
    of the doc stays. Each fact goes in one doc only: never copy notes from one doc into the other.
    Add only what outlasts this task: how the student writes, the teacher's rules, mistakes they
    repeat, what they prefer. Never the task's content or its answer. Keep notes short.
    """
    deps = ctx.deps
    context = deps.context
    if context is None:
        return MemoryResult(saved=False, message=MEMORY_UNAVAILABLE)
    scope = notes.scope
    if not notes.add and not notes.remove:
        raise ModelRetry("Nothing to change: give notes to add, or earlier notes to remove.")
    for _ in range(MAX_SAVE_ATTEMPTS):
        body = deps.notes.get(scope, "")
        new_body, unmatched = _apply_notes(body, notes.add, notes.remove)
        if unmatched:
            raise ModelRetry(
                f"These notes to remove are not in the {scope} notes: {unmatched!r}. "
                f"The notes are now:\n\n{_shown(body)}\n\n"
                "Copy the notes to remove exactly, and call again."
            )
        if new_body == "\n".join(line for line in body.splitlines() if line.strip()):
            return MemoryResult(
                saved=True,
                message=f"Nothing changed, the {scope} notes already are:\n\n{_shown(body)}",
            )
        try:
            saved = await context.update_memory(scope, None, new_body, deps.versions.get(scope, 0))
        except ValidationError as error:
            reasons = "; ".join(f"{e['loc'][-1]}: {e['msg']}" for e in error.errors())
            raise ModelRetry(
                f"The notes were refused ({reasons}). Fix them and call again."
            ) from error
        except httpx2.HTTPStatusError as error:
            if error.response.status_code != 412:
                raise
            await _refresh_notes(deps, context, scope)  # changed meanwhile: apply again on it
            continue
        deps.versions[scope] = saved.version
        deps.notes[scope] = saved.content_md
        return MemoryResult(
            saved=True, message=f"Saved. The {scope} notes are now:\n\n{_shown(saved.content_md)}"
        )
    return MemoryResult(
        saved=False,
        message=(
            f"Not saved: the {scope} notes kept changing meanwhile. "
            f"They are now:\n\n{_shown(deps.notes.get(scope, ''))}\n\nCall update_memory again."
        ),
    )


class Unavailable(BaseModel):
    """What the model is told when a memory tool has no Context API to ask."""

    message: str = MEMORY_UNAVAILABLE


async def read_memory(ctx: RunContext[Deps], request: GetSubjectMemory) -> Memory | Unavailable:
    """Read the notes on the student in a subject (default: this job's subject)."""
    if ctx.deps.context is None:
        return Unavailable()
    memory = await ctx.deps.context.get_subject_memory(request.subject)
    if request.subject is None or request.subject == ctx.deps.subject:
        ctx.deps.versions["subject"] = memory.version
        ctx.deps.notes["subject"] = memory.content_md
    return memory


async def search_memory(
    ctx: RunContext[Deps], request: SearchHistory
) -> list[Memory] | Unavailable:
    """Search the notes on the student, in every subject, for a word or phrase."""
    if ctx.deps.context is None:
        return Unavailable()
    return await ctx.deps.context.search_history(request.query)


async def list_history(
    ctx: RunContext[Deps], request: ListHistory
) -> list[HistoryItem] | Unavailable:
    """List the student's other homework, newest first, with the grade they said they got."""
    if ctx.deps.context is None:
        return Unavailable()
    return await ctx.deps.context.list_history(request.limit)


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
        update_memory,
        read_memory,
        search_memory,
        list_history,
    ],
    retries=2,
)


@dataclass
class Checkpoint:
    """A conversation up to the answer of a round, and the rounds whose feedback it holds."""

    rounds: tuple[int, ...]
    messages: list[ModelMessage]


# job id -> round -> the checkpoint after the answer of that round
histories: OrderedDict[UUID, dict[int, Checkpoint]] = OrderedDict()
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
            return await _answer(body, model, body.text, None, None, feedback, None, None)
        async with ContextClient(body.context.url, body.context.token) as context:
            task = await context.get_task()
            profile = await context.get_profile()
            subject_memory = await context.get_subject_memory()
            feedback = round_feedback(task, body.round)
            answered = tuple(
                r.number for r in task.rounds if r.outcome == "answered" and r.number < body.round
            )
            return await _answer(
                body, model, task.text, profile, subject_memory, feedback, context, answered
            )


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
        logger.info("Round %s of job %s continues round %s", body.round, body.job_id, chosen)
    else:
        prompt, history = (
            task_message(
                text, profile, feedback, subject_memory.content_md if subject_memory else ""
            ),
            None,
        )
        rounds = (*(answered or ()), body.round)
        logger.info(
            "Round %s of job %s starts from the task and %d feedback",
            body.round,
            body.job_id,
            len(feedback),
        )
    deps = Deps(context)
    if profile is not None and subject_memory is not None:
        deps.versions = {"profile": profile.profile_version, "subject": subject_memory.version}
        deps.notes = {"profile": profile.profile_md, "subject": subject_memory.content_md}
        deps.subject = profile.subject
    result = await agent.run(
        prompt,
        model=model,
        deps=deps,
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
        "Round %s of job %s: %d model requests, tools %s, provider %s, tokens in %d out %d",
        body.round, body.job_id, len(responses), tools, providers,
        usage.input_tokens, usage.output_tokens,
    )  # fmt: skip
