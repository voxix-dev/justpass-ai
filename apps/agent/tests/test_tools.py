import asyncio
from typing import get_args

import httpx2
import pytest

from agent.fakes import FakeContextClient
from agent.roles import ROLE_TOOLS
from agent.tools import (
    TOOL_ARGS,
    GetProfile,
    GetSubjectMemory,
    HttpContextClient,
    ReadArtifact,
    RunAgent,
    RunCode,
    SandboxClient,
    ToolError,
    UnavailableTool,
    UpdateMemory,
    WriteArtifact,
    tool_spec,
)
from shared.models import Role, Tier


def test_every_role_tool_has_arguments_and_run_agent_matches_the_enums():
    assert set().union(*ROLE_TOOLS.values()) <= set(TOOL_ARGS)
    role_field, tier_field = RunAgent.model_fields["role"], RunAgent.model_fields["tier"]
    assert set(get_args(role_field.annotation)) == set(Role) - {Role.ORCHESTRATOR}
    assert set(get_args(tier_field.annotation)) == set(Tier)


def test_tool_spec_is_openai_function_format():
    spec = tool_spec("read_artifact")
    assert spec["type"] == "function"
    assert spec["function"]["name"] == "read_artifact"  # type: ignore[index]


def test_fake_context_client_round_trips_artifacts():
    ctx = FakeContextClient()
    written = asyncio.run(ctx.call("write_artifact", WriteArtifact(title="t", content_md="hi")))
    assert written == {"artifact_id": "art-1"}
    assert asyncio.run(ctx.call("read_artifact", ReadArtifact(artifact_id="art-1"))) == "hi"
    with pytest.raises(ToolError):
        asyncio.run(ctx.call("read_artifact", ReadArtifact(artifact_id="nope")))


def test_fake_context_client_keeps_memory():
    ctx = FakeContextClient()
    asyncio.run(ctx.call("update_memory", UpdateMemory(scope="profile", content_md="College")))
    math = UpdateMemory(scope="subject", subject="math", content_md="Show units")
    asyncio.run(ctx.call("update_memory", math))
    assert asyncio.run(ctx.call("get_profile", GetProfile())) == "College"
    assert asyncio.run(ctx.call("get_subject_memory", GetSubjectMemory(subject="math"))) == (
        "Show units"
    )


def test_unbuilt_tools_say_so():
    with pytest.raises(ToolError, match="web_search is not available yet"):
        asyncio.run(UnavailableTool().call("web_search", GetProfile()))


def test_http_context_client_sends_the_token_and_maps_errors():
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if len(seen) == 3:
            return httpx2.Response(200, text="<html>")
        return httpx2.Response(403 if len(seen) > 1 else 200, json="profile")

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    ctx = HttpContextClient("http://api/", "job-token", http)
    assert asyncio.run(ctx.call("get_profile", GetProfile())) == "profile"
    assert str(seen[0].url) == "http://api/v1/context/get_profile"
    assert seen[0].headers["authorization"] == "Bearer job-token"
    with pytest.raises(ToolError, match="403"):
        asyncio.run(ctx.call("get_profile", GetProfile()))
    with pytest.raises(ToolError, match="invalid JSON"):
        asyncio.run(ctx.call("get_profile", GetProfile()))


def test_sandbox_client_posts_the_code_and_checks_the_result():
    result = {"exit_code": 0, "stdout": "4\n", "stderr": "", "timed_out": False}
    replies = [
        httpx2.Response(200, json=result | {"output_truncated": False}),
        httpx2.Response(200, json=result),  # a field missing
        httpx2.Response(502),
    ]
    seen: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return replies[len(seen) - 1]

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    sandbox = SandboxClient("http://api/v1/context/run_code", {"Authorization": "Bearer t"}, http)
    out = asyncio.run(sandbox.call("run_code", RunCode(code="print(2 + 2)")))
    assert out == result | {"output_truncated": False}
    assert seen[0].headers["authorization"] == "Bearer t"
    assert seen[0].content == b'{"code":"print(2 + 2)"}'
    with pytest.raises(ToolError, match="invalid result"):
        asyncio.run(sandbox.call("run_code", RunCode(code="1")))
    with pytest.raises(ToolError, match="HTTP 502"):
        asyncio.run(sandbox.call("run_code", RunCode(code="1")))


def test_sandbox_client_maps_network_errors():
    def handle(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    with pytest.raises(ToolError, match="unreachable: ConnectError"):
        asyncio.run(SandboxClient("http://x/run", {}, http).call("run_code", RunCode(code="1")))
