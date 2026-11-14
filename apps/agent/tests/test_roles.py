import contextlib
import importlib
import importlib.util
import json
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.models import make_models, reported_cost, web_searches
from agent.roles import WORKERS, curator, general, math, writer
from agent.web_search import WEB_SEARCH


def tool_names(agent: Agent[Any, Any]) -> set[str]:
    seen: list[set[str]] = []

    def record(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append({tool.name for tool in info.function_tools})
        return ModelResponse(parts=[TextPart("{}")])

    # structured output may reject the stub reply; the tools were recorded by then
    with agent.override(model=FunctionModel(record)), contextlib.suppress(Exception):
        agent.run_sync("x", deps=general.Deps(context=None))
    return seen[0]


def sent_tools(agent: Agent[Any, Any]) -> list[dict[str, Any]]:
    """The tools in the first request the agent sends to OpenRouter."""
    sent: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        completion = {
            "id": "gen-1",
            "object": "chat.completion",
            "created": 1,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Hello"},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx2.Response(200, json=completion)

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    model = make_models(SecretStr("k"), http)["general"]
    with contextlib.suppress(Exception):  # a structured agent may reject the plain reply
        agent.run_sync("x", model=model, deps=general.Deps(context=None))
    return sent[0].get("tools", [])


MATH_TOOLS = {"evaluate", "simplify", "solve", "differentiate", "integrate"}
MEMORY_TOOLS = {"write_document", "read_memory", "search_memory", "list_history"}


def test_general_tools():
    assert tool_names(general.agent) == MATH_TOOLS | MEMORY_TOOLS


def test_math_tools():
    assert tool_names(math.agent) == MATH_TOOLS


def test_writer_has_no_tools():
    assert tool_names(writer.agent) == set()


def test_curator_agents_have_no_tools():
    assert tool_names(curator.agent) == set()
    assert tool_names(curator.subject_agent) == set()


@pytest.mark.skipif(
    importlib.util.find_spec("agent.roles.orchestrator") is None,
    reason="orchestrator role not written yet",
)
def test_orchestrator_tools():
    orchestrator = importlib.import_module("agent.roles.orchestrator")
    assert tool_names(orchestrator.agent) == {"run_agent"}
    assert not [t for t in sent_tools(orchestrator.agent) if t["type"] != "function"]


@pytest.mark.parametrize("role", ["general", "math", "writer"])
def test_workers_ask_openrouter_for_web_search(role: Any):
    server = [t for t in sent_tools(WORKERS[role]) if t["type"] != "function"]
    assert len(server) == 1
    assert server[0]["type"] == "openrouter:web_search"
    assert server[0]["parameters"]["max_uses"] == 2
    assert server[0]["parameters"]["max_results"] == 5


def test_only_workers_carry_web_search():
    for agent in (curator.agent, curator.subject_agent):
        assert not [t for t in sent_tools(agent) if t["type"] != "function"]
    assert WEB_SEARCH.tool.max_uses == 2  # type: ignore[union-attr]


def test_reported_cost_and_searches():
    messages: list[ModelMessage] = [
        ModelResponse(
            parts=[],
            provider_details={"cost": 0.01, "server_tool_use": {"web_search_requests": 2}},
        ),
        ModelResponse(parts=[]),
    ]
    assert reported_cost(messages) == pytest.approx(0.01)
    assert web_searches(messages) == 2
    assert reported_cost([]) == 0
