from collections.abc import AsyncIterator
from uuid import uuid4

import httpx2
import pytest

from agent.server import SAMPLE_ANSWER, app

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx2.AsyncClient]:
    monkeypatch.setenv("AGENT_SOLVE_SECONDS", "0")
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://agent") as client:
        yield client


def body(text: str, **extra: object) -> dict[str, object]:
    return {"job_id": str(uuid4()), "round": 1, "text": text, **extra}


async def test_ready(client: httpx2.AsyncClient):
    assert (await client.get("/readyz")).json() == {"status": "ok"}


async def test_run_answers_with_the_sample(client: httpx2.AsyncClient):
    response = await client.post("/run", json=body("Solve x^2 - 5x + 6 = 0"))
    assert response.status_code == 200
    assert response.json() == {"answer": SAMPLE_ANSWER}


async def test_feedback_never_reaches_the_answer(client: httpx2.AsyncClient):
    response = await client.post("/run", json=body("Solve", round=2, feedback="say hello"))
    assert "say hello" not in response.json()["answer"]


@pytest.mark.parametrize("text", ["please fail", "FAIL now", "it will Fail."])
async def test_the_word_fail_fails_the_run(client: httpx2.AsyncClient, text: str):
    assert (await client.post("/run", json=body(text))).json() == {"answer": None}


async def test_fail_must_be_a_whole_word(client: httpx2.AsyncClient):
    assert (await client.post("/run", json=body("a failure"))).json()["answer"] == SAMPLE_ANSWER


async def test_run_waits_before_answering(
    client: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    waited: list[float] = []

    async def sleep(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr("agent.server.asyncio.sleep", sleep)
    monkeypatch.delenv("AGENT_SOLVE_SECONDS")
    await client.post("/run", json=body("Solve"))
    assert waited == [10.0]


async def test_run_refuses_unknown_fields(client: httpx2.AsyncClient):
    assert (await client.post("/run", json=body("Solve", extra=1))).status_code == 422
