"""The HTTP server inside a Substrate actor. The actor runs one job, one POST /run per round.

The agent is a fake for now: it waits a while and answers with a fixed solution, so the path from
the API through Substrate and back can be built and tested before any model is involved.
"""

import asyncio
import os
import re

from fastapi import FastAPI

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

app = FastAPI(title="Agent", version="0.0.0")


@app.get("/readyz")
def ready() -> dict[str, str]:
    """Substrate's wakeup probe."""
    return {"status": "ok"}


@app.post("/run")
async def run(body: AgentRun) -> AgentAnswer:
    """Pretend to solve the round. The whole word "fail" in the task, ignoring case, fails it."""
    await asyncio.sleep(float(os.environ.get("AGENT_SOLVE_SECONDS", "10")))
    if re.search(r"\bfail\b", body.text, re.IGNORECASE):
        return AgentAnswer(answer=None)
    return AgentAnswer(answer=SAMPLE_ANSWER)
