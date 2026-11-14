"""The general role: it answers the student's homework, with math tools, a document tool and tools
to read the notes on the student."""

from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext

from agent import math_tools
from agent.context import ContextClient
from agent.prompt import FORMAT_RULES, PART_RULE
from agent.web_search import WEB_SEARCH, WEB_SEARCH_RULE
from shared.context import (
    GetSubjectMemory,
    HistoryItem,
    ListHistory,
    Memory,
    SearchHistory,
    Text,
)

SYSTEM_PROMPT = (
    "You solve a student's homework.\n\n"
    + FORMAT_RULES
    + PART_RULE
    + r"""- Show the steps, as briefly as the notes on the student ask.
- Use the math tools to calculate, simplify, solve, differentiate and integrate, and to check your
  work, instead of doing it in your head.
- Use write_document only for a separate document, such as a study sheet. Your reply is the answer.
"""
    + WEB_SEARCH_RULE
    + r"""- Notes on the student are saved for you after each round; you do not save them.
- Use search_memory, list_history and read_memory when earlier knowledge of the student could help.
"""
)


DOCUMENTS_UNAVAILABLE = "Documents are unavailable in this round. Put everything in your answer."
MEMORY_UNAVAILABLE = "Memory is unavailable in this round."


@dataclass
class Deps:
    """What the tools reach: the Context API of this round, or None when there is none."""

    context: ContextClient | None
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


class Unavailable(BaseModel):
    """What the model is told when a memory tool has no Context API to ask."""

    message: str = MEMORY_UNAVAILABLE


async def read_memory(ctx: RunContext[Deps], request: GetSubjectMemory) -> Memory | Unavailable:
    """Read the notes on the student in a subject (default: this job's subject)."""
    if ctx.deps.context is None:
        return Unavailable()
    return await ctx.deps.context.get_subject_memory(request.subject or ctx.deps.subject)


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
        read_memory,
        search_memory,
        list_history,
    ],
    capabilities=[WEB_SEARCH],
    retries=2,
)
