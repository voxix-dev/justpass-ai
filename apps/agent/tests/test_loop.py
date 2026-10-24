import asyncio
from functools import partial

import pytest
from pydantic import BaseModel, JsonValue

from agent.config import Limits
from agent.fakes import FakeContextClient, ScriptedModel, call, say
from agent.loop import AgentError, Budget, BudgetExceeded, ToolHandler, run_agent_loop
from agent.roles import ROLE_TOOLS
from shared.models import Role


def run(
    model: ScriptedModel,
    ctx: FakeContextClient,
    limits: Limits | None = None,
    handlers: dict[str, ToolHandler] | None = None,
) -> str:
    """Run a research agent whose handlers include get_profile, which it must still not get."""
    names = ["web_search", "web_fetch", "write_artifact", "get_profile"]
    handlers = {name: partial(ctx.call, name) for name in names} | (handlers or {})
    coro = run_agent_loop(
        role=Role.RESEARCH,
        model="m",
        system="You are the research agent.",
        user="Research WW1.",
        llm=model,
        budget=Budget(limits or Limits()),
        session_id="job-1",
        handlers=handlers,
    )
    return asyncio.run(coro)


def tool_replies(model: ScriptedModel) -> list[str]:
    return [m.content or "" for r in model.requests for m in r.messages if m.role == "tool"]


def test_research_cannot_call_get_profile_even_if_the_model_asks():
    ctx = FakeContextClient()
    model = ScriptedModel({Role.RESEARCH: [call("get_profile", {}), say("done")]})
    assert run(model, ctx) == "done"
    assert ctx.calls == []
    assert "not available to the research agent" in tool_replies(model)[0]
    offered = {t["function"]["name"] for t in model.requests[0].tools}  # type: ignore[index]
    assert offered == ROLE_TOOLS[Role.RESEARCH]


def test_malformed_and_unknown_tool_calls_become_errors_for_the_model():
    model = ScriptedModel(
        {
            Role.RESEARCH: [
                call("write_artifact", "{not json"),
                call("write_artifact", {"title": "notes"}),
                call("rm_rf", {}),
                say("gave up"),
            ]
        }
    )
    assert run(model, FakeContextClient()) == "gave up"
    first, second, third = tool_replies(model)[-3:]
    assert "invalid arguments for write_artifact" in first
    assert "content_md: Field required" in second
    assert "'rm_rf' is not available" in third


def test_reasoning_details_are_sent_back_unchanged():
    reasoning: list[dict[str, JsonValue]] = [
        {"type": "reasoning.encrypted", "data": "opaque==", "format": "x", "index": 0}
    ]
    first = call("write_artifact", {"title": "notes", "content_md": "WW1 notes"})
    first.reasoning_details = reasoning
    model = ScriptedModel({Role.RESEARCH: [first, say("done")]})
    run(model, FakeContextClient())
    assert model.requests[1].messages[2].reasoning_details == reasoning


def test_budget_aborts_the_job():
    model = ScriptedModel({Role.RESEARCH: [call("web_search", {"query": "x"})] * 10})
    with pytest.raises(BudgetExceeded):
        run(model, FakeContextClient(), Limits(max_cost_usd=0.025))
    assert len(model.requests) == 3


def test_tool_calls_in_one_turn_run_concurrently():
    barrier = asyncio.Barrier(2)  # deadlocks (and times out) if the calls ran one by one

    async def meet(args: BaseModel) -> JsonValue:
        async with asyncio.timeout(1):
            await barrier.wait()
        return "met"

    both = call("web_search", {"query": "a"})
    both.tool_calls = [
        *(both.tool_calls or []),
        *(call("web_fetch", {"url": "b"}).tool_calls or []),
    ]
    model = ScriptedModel({Role.RESEARCH: [both, say("done")]})
    assert (
        run(model, FakeContextClient(), handlers={"web_search": meet, "web_fetch": meet}) == "done"
    )
    assert tool_replies(model) == ["met", "met"]


def test_agent_turn_limit():
    model = ScriptedModel({Role.RESEARCH: [call("web_search", {"query": "x"})] * 10})
    with pytest.raises(AgentError, match="used all 2 turns"):
        run(model, FakeContextClient(), Limits(max_turns_per_agent=2))
