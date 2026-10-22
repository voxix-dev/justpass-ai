import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from agent.llm import ChatRequest, LLMError, Message, OpenRouterClient

RECORDED = (Path(__file__).parent / "recorded" / "openrouter_tool_call.json").read_bytes()


def client(
    *responses: httpx2.Response | Exception,
) -> tuple[OpenRouterClient, list[dict[str, Any]]]:
    """A client whose HTTP calls get the given responses in order, and the bodies it sent."""
    queue, sent = list(responses), list[dict[str, Any]]()

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content) | {"_auth": request.headers.get("authorization")})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    return OpenRouterClient("sk-test", timeout_s=5, max_retries=2, backoff_s=0, http=http), sent


def request(*messages: Message) -> ChatRequest:
    msgs = [Message(role="user", content="hi"), *messages]
    return ChatRequest(model="m", messages=msgs, tools=[], session_id="job-1", max_tokens=100)


def test_reasoning_details_go_back_unchanged():
    llm, sent = client(
        httpx2.Response(200, content=RECORDED), httpx2.Response(200, content=RECORDED)
    )
    first = asyncio.run(llm.complete(request()))
    asyncio.run(llm.complete(request(first.message)))

    recorded = json.loads(RECORDED)["choices"][0]["message"]["reasoning_details"]
    assert sent[1]["messages"][1]["reasoning_details"] == recorded
    assert first.usage.cost == pytest.approx(0.0098)


def test_request_turns_on_caching_zdr_and_session():
    llm, sent = client(httpx2.Response(200, content=RECORDED))
    asyncio.run(llm.complete(request()))
    assert sent[0]["cache_control"] == {"type": "ephemeral"}
    assert sent[0]["provider"] == {"zdr": True}
    assert sent[0]["session_id"] == "job-1"
    assert sent[0]["_auth"] == "Bearer sk-test"


def test_retries_rate_limits_server_errors_and_timeouts():
    llm, sent = client(
        httpx2.Response(429), httpx2.ReadTimeout("slow"), httpx2.Response(200, content=RECORDED)
    )
    asyncio.run(llm.complete(request()))
    assert len(sent) == 3


def test_gives_up_after_max_retries():
    llm, sent = client(*[httpx2.Response(503)] * 3)
    with pytest.raises(LLMError, match="after 3 attempts"):
        asyncio.run(llm.complete(request()))
    assert len(sent) == 3


def test_client_errors_are_not_retried():
    llm, sent = client(httpx2.Response(400, text="bad model"))
    with pytest.raises(LLMError, match="400"):
        asyncio.run(llm.complete(request()))
    assert len(sent) == 1


def test_malformed_response_is_an_error():
    llm, _ = client(httpx2.Response(200, json={"choices": []}))
    with pytest.raises(LLMError, match="malformed"):
        asyncio.run(llm.complete(request()))
