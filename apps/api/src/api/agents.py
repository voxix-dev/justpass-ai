"""Reaching the agents, and nothing about jobs: dispatcher.py decides when each call happens.

SubstrateAgents gives every job its own actor. SingleAgent sends every job to one plain server, for
compose, CI and laptops, which have no Substrate.
"""

import asyncio
import os
import ssl
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import grpc
import httpx2
from google.protobuf.message import Message

from api import ate_pb2
from shared.agent_run import AgentAnswer, AgentRun

CONTROL_TIMEOUT = 30.0
# A run has no overall limit, because a real agent takes as long as it takes. Instead /run writes a
# byte every few seconds while it works, and no byte for IDLE_TIMEOUT seconds means it is dead. The
# same wait covers the router waking a suspended actor before the first byte.
IDLE_TIMEOUT = 60.0
# httpx's read timeout is the gap between bytes, not the whole reply.
RUN_TIMEOUT = httpx2.Timeout(CONTROL_TIMEOUT, read=IDLE_TIMEOUT)
BUSY_WAIT = 60.0  # how long a job waits for a free worker before it counts as a failure
BUSY_RETRY_DELAY = 2.0


class Agents(Protocol):
    async def solve(self, run: AgentRun) -> AgentAnswer:
        """Run one round; an answer may contain None if the agent could not solve it.

        Raises if no valid reply came back, whether or not the agent got the task.
        """
        ...

    async def hibernate(self, job_id: uuid.UUID) -> None:
        """Free the job's agent until the student replies. It keeps what it knows."""
        ...

    async def release(self, job_id: uuid.UUID) -> None:
        """Drop the job's agent for good."""
        ...


async def _post_run(
    client: httpx2.AsyncClient, url: str, run: AgentRun, headers: dict[str, str] | None = None
) -> AgentAnswer:
    """Post a round and validate the reply, propagating HTTP, transport, and validation errors.

    The agent's reply starts with heartbeat newlines, which JSON allows before its value. If the
    agent fails mid-run the stream ends without an answer: that is a transport or validation error
    here, which the dispatcher counts as a failed attempt.
    """
    response = await client.post(
        f"{url}/run",
        content=run.model_dump_json(),
        headers={"content-type": "application/json", **(headers or {})},
    )
    response.raise_for_status()
    return AgentAnswer.model_validate_json(response.content)


@dataclass
class SingleAgent:
    """One agent server shared by all jobs, so there is nothing to hibernate or release."""

    url: str
    transport: httpx2.AsyncBaseTransport | None = None  # tests only

    async def solve(self, run: AgentRun) -> AgentAnswer:
        async with httpx2.AsyncClient(timeout=RUN_TIMEOUT, transport=self.transport) as client:
            return await _post_run(client, self.url, run)

    async def hibernate(self, job_id: uuid.UUID) -> None:
        pass

    async def release(self, job_id: uuid.UUID) -> None:
        pass


@dataclass
class Control:
    """The ate-api calls we need. ate-api speaks only gRPC, so these are not plain HTTP.

    Each call opens its own channel and reads the token again: calls are few, and the token and the
    CA rotate on disk.
    """

    open_channel: Callable[[], grpc.aio.Channel]
    token_file: Path

    async def _call(self, method: str, request: Message) -> None:
        """Call ate-api with the current token; file and gRPC errors propagate."""
        token = self.token_file.read_text().strip()
        async with self.open_channel() as channel:
            call = channel.unary_unary(
                f"/ateapi.Control/{method}",
                request_serializer=type(request).SerializeToString,
                response_deserializer=lambda _: None,  # nothing in the reply is used
            )
            await call(
                request, metadata=[("authorization", f"Bearer {token}")], timeout=CONTROL_TIMEOUT
            )

    async def create_actor(self, atespace: str, name: str, template: str) -> None:
        actor = ate_pb2.Actor(
            metadata=ate_pb2.ResourceMetadata(atespace=atespace, name=name),
            actor_template=ate_pb2.ObjectRef(atespace=atespace, name=template),
        )
        await self._call("CreateActor", ate_pb2.CreateActorRequest(actor=actor))

    async def suspend_actor(self, atespace: str, name: str) -> None:
        ref = ate_pb2.ObjectRef(atespace=atespace, name=name)
        await self._call("SuspendActor", ate_pb2.SuspendActorRequest(actor=ref))

    async def delete_actor(self, atespace: str, name: str) -> None:
        ref = ate_pb2.ObjectRef(atespace=atespace, name=name)
        await self._call("DeleteActor", ate_pb2.DeleteActorRequest(actor=ref))


def _actor_name(job_id: uuid.UUID) -> str:
    return f"job-{job_id}"


@dataclass
class SubstrateAgents:
    """One actor per job: every round of a job goes to the same actor, which keeps its memory.

    The router wakes a suspended actor when a request arrives, so feedback needs no resume call.
    """

    control: Control
    router: Callable[[], httpx2.AsyncClient]
    router_url: str
    atespace: str
    template: str
    busy_wait: float = BUSY_WAIT

    async def solve(self, run: AgentRun) -> AgentAnswer:
        """Create or reuse the job's actor and send it a round through the router.

        Retry router 503 responses until busy_wait seconds have elapsed; other HTTP errors and
        actor creation errors other than ALREADY_EXISTS propagate, as do transport and reply
        validation errors. A 503 at or after the deadline also propagates.
        """
        actor = _actor_name(run.job_id)
        try:
            await self.control.create_actor(self.atespace, actor, self.template)
        except grpc.aio.AioRpcError as error:
            if error.code() != grpc.StatusCode.ALREADY_EXISTS:  # a later round, or a retry
                raise
        target = {"ate-target-actor": f"{self.atespace}/{actor}"}
        deadline = asyncio.get_running_loop().time() + self.busy_wait
        async with self.router() as client:
            while True:
                try:
                    return await _post_run(client, self.router_url, run, target)
                except httpx2.HTTPStatusError as error:
                    # An actor fills its worker, so the router answers 503 when all are taken.
                    busy = error.response.status_code == httpx2.codes.SERVICE_UNAVAILABLE
                    if not busy or asyncio.get_running_loop().time() >= deadline:
                        raise
                await asyncio.sleep(BUSY_RETRY_DELAY)

    async def hibernate(self, job_id: uuid.UUID) -> None:
        await self.control.suspend_actor(self.atespace, _actor_name(job_id))

    async def release(self, job_id: uuid.UUID) -> None:
        """Delete the actor, ignoring NOT_FOUND and propagating other gRPC errors."""
        try:
            await self.control.delete_actor(self.atespace, _actor_name(job_id))
        except grpc.aio.AioRpcError as error:
            if error.code() != grpc.StatusCode.NOT_FOUND:
                raise


def _substrate_from_env(router_url: str) -> SubstrateAgents:
    ca_file = Path(os.environ["ATE_CA_FILE"])
    client_cert = os.environ["ATE_CLIENT_CERT_FILE"]

    def open_channel() -> grpc.aio.Channel:
        credentials = grpc.ssl_channel_credentials(ca_file.read_bytes())
        return grpc.aio.secure_channel(os.environ["ATE_API_TARGET"], credentials)

    def router() -> httpx2.AsyncClient:
        tls = ssl.create_default_context(cafile=ca_file)
        tls.load_cert_chain(client_cert)
        return httpx2.AsyncClient(verify=tls, timeout=RUN_TIMEOUT)

    return SubstrateAgents(
        control=Control(open_channel, Path(os.environ["ATE_TOKEN_FILE"])),
        router=router,
        router_url=router_url,
        atespace=os.environ["ATE_ATESPACE"],
        template=os.environ["ATE_TEMPLATE"],
    )


def agents_from_env() -> Agents:
    """Select the plain agent when AGENT_URL is set, otherwise configure Substrate.

    Raises RuntimeError if neither URL is set and KeyError if a required ATE_* setting is missing.
    """
    if url := os.environ.get("AGENT_URL"):
        return SingleAgent(url)
    if router_url := os.environ.get("ATE_ROUTER_URL"):
        return _substrate_from_env(router_url)
    raise RuntimeError("Set AGENT_URL or the ATE_* settings, see .env.example.")
