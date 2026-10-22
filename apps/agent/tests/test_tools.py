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
    with pytest.raises(ToolError, match="run_code is not available yet"):
        asyncio.run(UnavailableTool().call("run_code", GetProfile()))


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
