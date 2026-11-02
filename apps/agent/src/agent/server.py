"""The HTTP server inside a Substrate actor. The actor runs one job, one POST /run per round.

The agent is a fake for now: it waits a while and answers with a fixed solution, so the path from
the API through Substrate and back can be built and tested before any model is involved.
"""

import asyncio
import re
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

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

app = FastAPI(title="Agent", version="0.0.0")


@app.get("/readyz")
def ready() -> dict[str, str]:
    """Substrate's wakeup probe."""
    return {"status": "ok"}


async def _solve(body: AgentRun) -> AgentAnswer:
    await asyncio.sleep(SOLVE_SECONDS)
    if re.search(r"\bfail\b", body.text, re.IGNORECASE):
        return AgentAnswer(answer=None)
    return AgentAnswer(answer=SAMPLE_ANSWER)


async def _stream(body: AgentRun) -> AsyncIterator[bytes]:
    """Write a newline every HEARTBEAT_SECONDS while solving, then the answer as JSON."""
    work = asyncio.create_task(_solve(body))
    try:
        while not (await asyncio.wait({work}, timeout=HEARTBEAT_SECONDS))[0]:
            yield b"\n"
        yield work.result().model_dump_json().encode()  # an error here ends the stream without JSON
    finally:
        work.cancel()  # the caller hung up


@app.post("/run", response_class=StreamingResponse)
async def run(body: AgentRun) -> StreamingResponse:
    """Stream a fixed answer after SOLVE_SECONDS, with a heartbeat while it works.

    The reply is 200 application/json whose body is whitespace (one newline per HEARTBEAT_SECONDS)
    followed by an AgentAnswer. Leading whitespace is valid JSON, so a client that reads the whole
    body and parses it works unchanged, while one that watches the stream can tell the agent is
    alive. If the work raises, the stream ends without JSON and the caller counts a failed attempt.

    The whole word "fail" in the task, ignoring case, returns an answer of None. Feedback does
    not affect the answer.
    """
    return StreamingResponse(_stream(body), media_type="application/json")
