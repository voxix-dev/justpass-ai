"""The HTTP server inside a Substrate actor. The actor runs one job, one POST /run per round.

The agent is a fake for now: it waits a while and answers with a fixed solution, so the path from
the API through Substrate and back can be built and tested before any model is involved.
"""

import asyncio
import logging
import re
from collections import OrderedDict
from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from agent.context import ContextClient
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

SOLVE_SECONDS = 10  # the fake's pretend work; goes away with the fake
HEARTBEAT_SECONDS = 5  # how often /run writes a byte while working, so the API sees it is alive

MAX_RUNS = 64  # finished and running rounds remembered, so a retried /run joins instead of redoing

logger = logging.getLogger(__name__)
app = FastAPI(title="Agent", version="0.0.0")


@app.get("/readyz")
def ready() -> dict[str, str]:
    """Substrate's wakeup probe."""
    return {"status": "ok"}


def _fence(text: str) -> str:
    """Put text in a fenced code block longer than any run of backticks inside it.

    The student's words are shown as they are: a fence they cannot close keeps them from
    becoming Markdown of their own.
    """
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def _answer(feedback: list[str]) -> str:
    if not feedback:
        return SAMPLE_ANSWER
    changes = "\n\n".join(_fence(text) for text in feedback)
    return f"{SAMPLE_ANSWER}\n### Changes after your feedback\n\n{changes}\n"


def _feedback_of(body: AgentRun) -> list[str]:
    return [body.feedback] if body.feedback else []


async def _solve_with_context(grant_url: str, token: str, round_number: int) -> AgentAnswer:
    async with ContextClient(grant_url, token) as context:
        task = await context.get_task()
        profile = await context.get_profile()
        feedback = [r.feedback for r in task.rounds if r.feedback and r.number <= round_number]
        if re.search(r"\bfail\b", task.text, re.IGNORECASE):
            return AgentAnswer(answer=None)
        answer = _answer(feedback)
        await context.write_draft(f"Draft for {profile.subject}", answer)
        return AgentAnswer(answer=answer)


async def solve(body: AgentRun) -> AgentAnswer:
    await asyncio.sleep(SOLVE_SECONDS)
    if body.context:
        return await _solve_with_context(body.context.url, body.context.token, body.round)
    if re.search(r"\bfail\b", body.text, re.IGNORECASE):
        return AgentAnswer(answer=None)
    return AgentAnswer(answer=_answer(_feedback_of(body)))


runs: OrderedDict[tuple[UUID, int], asyncio.Task[AgentAnswer]] = OrderedDict()


def join_run(body: AgentRun) -> asyncio.Task[AgentAnswer]:
    """The work for this round: start it, or join the one already running or finished."""
    key = (body.job_id, body.round)
    work = runs.get(key)
    if work is not None:
        runs.move_to_end(key)
        return work
    work = asyncio.create_task(solve(body))
    work.add_done_callback(lambda done: _forget_if_failed(key, done))
    runs[key] = work
    while len(runs) > MAX_RUNS:
        runs.popitem(last=False)
    return work


def _forget_if_failed(key: tuple[UUID, int], work: asyncio.Task[AgentAnswer]) -> None:
    """A run that raised is dropped, so a retry runs again."""
    if work.cancelled() or work.exception() is not None:
        if work.exception() is not None:
            logger.error("Run of %s round %s failed", key[0], key[1], exc_info=work.exception())
        if runs.get(key) is work:
            del runs[key]


async def stream(body: AgentRun) -> AsyncGenerator[bytes]:
    """Write a newline every HEARTBEAT_SECONDS while solving, then the answer as JSON."""
    work = join_run(body)  # a caller that hangs up leaves the work running for the retry
    while not (await asyncio.wait({work}, timeout=HEARTBEAT_SECONDS))[0]:
        yield b"\n"
    yield work.result().model_dump_json().encode()  # an error here ends the stream without JSON


@app.post("/run", response_class=StreamingResponse)
async def run(body: AgentRun) -> StreamingResponse:
    """Stream a fixed answer after SOLVE_SECONDS, with a heartbeat while it works.

    The reply is 200 application/json whose body is whitespace (one newline per HEARTBEAT_SECONDS)
    followed by an AgentAnswer. Leading whitespace is valid JSON, so a client that reads the whole
    body and parses it works unchanged, while one that watches the stream can tell the agent is
    alive. If the work raises, the stream ends without JSON and the caller counts a failed attempt.

    The whole word "fail" in the task, ignoring case, returns an answer of None. Feedback is shown
    in the answer, in a fenced block. A second call for a round that is running or done joins that
    run instead of starting another.
    """
    return StreamingResponse(stream(body), media_type="application/json")
