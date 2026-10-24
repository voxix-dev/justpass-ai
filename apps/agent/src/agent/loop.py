"""The agent loop: call the model, run the tools it asks for, repeat until it answers."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping

from pydantic import BaseModel, JsonValue, ValidationError

from agent.config import Limits
from agent.llm import ChatModel, ChatRequest, Message, ToolCall, Usage
from agent.roles import tools_for
from agent.tools import TOOL_ARGS, ToolError, tool_spec
from shared.models import Role

log = logging.getLogger(__name__)

type ToolHandler = Callable[[BaseModel], Awaitable[JsonValue]]


class BudgetExceeded(Exception):
    """The job hit a hard limit and must stop."""


class AgentError(Exception):
    """One agent ran out of turns."""


class Budget:
    """Model calls and spend for one whole job, shared by all its agents.

    A call's turn is reserved before it starts, its cost is added when it returns. Agents
    running in parallel can each start one call under the limit, so the cost can overshoot by
    that much; calls cancelled mid-flight and retried timeouts are billed but not counted.
    """

    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self.turns = 0
        self.cost_usd = 0.0

    def start_call(self) -> None:
        if self.turns >= self.limits.max_turns:
            raise BudgetExceeded(f"job used all {self.limits.max_turns} model calls")
        if self.cost_usd >= self.limits.max_cost_usd:
            raise BudgetExceeded(f"job spent ${self.cost_usd:.2f} of ${self.limits.max_cost_usd}")
        self.turns += 1

    def add(self, usage: Usage) -> None:
        self.cost_usd += usage.cost


async def run_agent_loop(
    *,
    role: Role,
    model: str,
    system: str,
    user: str,
    llm: ChatModel,
    budget: Budget,
    session_id: str,
    handlers: Mapping[str, ToolHandler],
) -> str:
    """Run one agent until it replies without tool calls; return that reply's text."""
    allowed = tools_for(role)
    if missing := allowed - handlers.keys():
        raise ValueError(f"no handler for {sorted(missing)}")
    messages = [Message(role="system", content=system), Message(role="user", content=user)]
    specs = [tool_spec(name) for name in sorted(allowed)]
    for _ in range(budget.limits.max_turns_per_agent):
        budget.start_call()
        request = ChatRequest(
            model=model,
            messages=messages,
            tools=specs,
            session_id=session_id,
            max_tokens=budget.limits.max_tokens_per_call,
        )
        completion = await llm.complete(request)
        budget.add(completion.usage)
        reply = completion.message
        messages.append(reply)  # as received, so reasoning_details go back unchanged
        if not reply.tool_calls:
            return reply.content or ""
        # Several calls in one turn (e.g. parallel run_agent) run concurrently.
        tasks = [asyncio.create_task(_execute(role, c, handlers)) for c in reply.tool_calls]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for call, result in zip(reply.tool_calls, results, strict=True):
            messages.append(Message(role="tool", tool_call_id=call.id, content=result))
    raise AgentError(f"{role} agent used all {budget.limits.max_turns_per_agent} turns")


async def _execute(role: Role, call: ToolCall, handlers: Mapping[str, ToolHandler]) -> str:
    """Run one tool call. Every problem becomes an error message for the model, not a crash."""
    name = call.function.name
    if name not in tools_for(role):
        log.warning("refused tool %r for role %s", name, role)
        return f"error: tool {name!r} is not available to the {role} agent"
    try:
        args = TOOL_ARGS[name].model_validate_json(call.function.arguments or "{}")
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, p['loc']))}: {p['msg']}" for p in e.errors())
        return f"error: invalid arguments for {name}: {problems}"
    try:
        result = await handlers[name](args)
    except ToolError as e:
        return f"error: {e}"
    return result if isinstance(result, str) else json.dumps(result)
