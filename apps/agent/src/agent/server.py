"""The HTTP server inside a Substrate actor. The actor runs one job, one POST /run per round.

A round that carries an OpenRouter key and no feedback goes to the model (solver.py), which works
from the task text alone. A feedback round with a key gets the fixed sample for now. A round
without a key gets a fake: it waits a while and answers with a fixed solution, so local
development, the tests and the cluster check run with no model and no cost.
"""

import asyncio
import logging
import re
from collections import OrderedDict
from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent.context import ContextClient
from agent.prompt import fence, round_feedback
from agent.solver import solve_with_model
from shared.agent_run import AgentAnswer, AgentRun

SAMPLE_ANSWER = r"""## Solution

We need the roots of $x^2 - 5x + 6 = 0$.

**1. Factor.** Look for two numbers that multiply to $6$ and add to $-5$: those are $-2$ and $-3$.

$$
x^2 - 5x + 6 = (x - 2)(x - 3)
$$

**2. Solve.** A product is zero when one factor is zero, so $x = 2$ or $x = 3$.

**3. Check** with the quadratic formula:

$$
x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a} = \frac{5 \pm \sqrt{25 - 24}}{2} = \frac{5 \pm 1}{2}
$$

> **Answer:** $x \in \{2, 3\}$

### Why it works
The sum of the roots is $-\tfrac{b}{a} = 5$ and the product is $\tfrac{c}{a} = 6$
(Vieta's formulas).
"""

SOLVE_SECONDS = 10  # the fake's pretend work
HEARTBEAT_SECONDS = 5  # how often /run writes a byte while working, so the API sees it is alive

MAX_RUNS = 64  # finished and running rounds remembered, so a retried /run joins instead of redoing

logger = logging.getLogger(__name__)
app = FastAPI(title="Agent", version="0.0.0")


class ValidationIssue(BaseModel):
    """One validation error, FastAPI's entry without `input`."""

    type: str
    loc: list[str | int]
    msg: str
    ctx: dict[str, object] | None = None
    url: str | None = None


class ValidationErrorBody(BaseModel):
    detail: list[ValidationIssue]


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
    """Answer 422 without echoing the input, which holds the OpenRouter key."""
    body = ValidationErrorBody(
        detail=[
            ValidationIssue(
                type=e["type"],
                loc=list(e["loc"]),
                msg=e["msg"],
                ctx=jsonable_encoder(e["ctx"]) if "ctx" in e else None,
                url=e.get("url"),
            )
            for e in error.errors()
        ]
    )
    return JSONResponse(body.model_dump(mode="json", exclude_none=True), status_code=422)


@app.get("/readyz")
def ready() -> dict[str, str]:
    """Substrate's wakeup probe."""
    return {"status": "ok"}


def _answer(feedback: list[str]) -> str:
    if not feedback:
        return SAMPLE_ANSWER
    changes = "\n\n".join(fence(text) for text in feedback)
    return f"{SAMPLE_ANSWER}\n### Changes after your feedback\n\n{changes}\n"


def _feedback_of(body: AgentRun) -> list[str]:
    return [body.feedback] if body.feedback else []


async def _solve_with_context(grant_url: str, token: str, round_number: int) -> AgentAnswer:
    async with ContextClient(grant_url, token) as context:
        task = await context.get_task()
        profile = await context.get_profile()
        feedback = round_feedback(task, round_number)
        if re.search(r"\bfail\b", task.text, re.IGNORECASE):
            return AgentAnswer(answer=None)
        answer = _answer(feedback)
        await context.write_draft(f"Draft for {profile.subject}", answer)
        return AgentAnswer(answer=answer)


async def solve(body: AgentRun) -> AgentAnswer:
    if body.openrouter_key is not None:
        if body.round > 1 or body.feedback:
            return AgentAnswer(answer=_answer(_feedback_of(body)))  # the model comes later
        return await solve_with_model(body)
    await asyncio.sleep(SOLVE_SECONDS)
    if body.context:
        return await _solve_with_context(body.context.url, body.context.token, body.round)
    if re.search(r"\bfail\b", body.text, re.IGNORECASE):
        return AgentAnswer(answer=None)
    return AgentAnswer(answer=_answer(_feedback_of(body)))


RunKey = tuple[UUID, int, UUID | None]  # job, round, lease

runs: OrderedDict[RunKey, asyncio.Task[AgentAnswer]] = OrderedDict()


def join_run(body: AgentRun) -> asyncio.Task[AgentAnswer]:
    """The work for this round under this lease: start it, or join the one running or finished.

    A new lease on the same round (another dispatcher claimed it) gets a run of its own: the old
    run holds a Context token for the old lease, whose writes the API refuses.
    """
    key: RunKey = (body.job_id, body.round, body.lease_id)
    work = runs.get(key)
    if work is not None:
        runs.move_to_end(key)
        return work
    work = asyncio.create_task(solve(body))
    work.add_done_callback(lambda done: _forget_if_failed(key, done))
    runs[key] = work
    _trim()
    return work


def _trim() -> None:
    """Forget the oldest finished runs beyond MAX_RUNS. A running one stays, so it is never lost."""
    for key in [key for key, work in runs.items() if work.done()]:
        if len(runs) <= MAX_RUNS:
            break
        del runs[key]


def _forget_if_failed(key: RunKey, work: asyncio.Task[AgentAnswer]) -> None:
    """A run that raised is dropped, so a retry runs again."""
    error = None if work.cancelled() else work.exception()
    if error is not None:
        logger.error("Run of %s round %s lease %s failed", *key, exc_info=error)
    if (work.cancelled() or error is not None) and runs.get(key) is work:
        del runs[key]


async def stream(body: AgentRun) -> AsyncGenerator[bytes]:
    """Write a newline every HEARTBEAT_SECONDS while solving, then the answer as JSON."""
    work = join_run(body)  # a caller that hangs up leaves the work running for the retry
    while not (await asyncio.wait({work}, timeout=HEARTBEAT_SECONDS))[0]:
        yield b"\n"
    yield work.result().model_dump_json().encode()  # an error here ends the stream without JSON


@app.post("/run", response_class=StreamingResponse)
async def run(body: AgentRun) -> StreamingResponse:
    """Stream the model's answer, or a fixed one, with a heartbeat while it works.

    The reply is 200 application/json whose body is whitespace (one newline per HEARTBEAT_SECONDS)
    followed by an AgentAnswer. Leading whitespace is valid JSON, so a client that reads the whole
    body and parses it works unchanged, while one that watches the stream can tell the agent is
    alive. If the work raises, the stream ends without JSON and the caller counts a failed attempt.

    A round with an `openrouter_key` and no feedback is answered by the model, one with feedback by
    the fixed sample. Without a key, the whole word "fail"
    in the task, ignoring case, returns an answer of None, and feedback is shown in the answer, in a
    fenced block. A second call for a round that is running or done under the
    same lease joins that run instead of starting another; a call under a new lease starts a new
    run.
    """
    return StreamingResponse(stream(body), media_type="application/json")
