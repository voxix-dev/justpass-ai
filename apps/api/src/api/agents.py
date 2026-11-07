"""Reaching the agents, and nothing about jobs: dispatcher.py decides when each call happens.

SubstrateAgents gives every job its own actor. SingleAgent sends every job to one plain server, for
compose, CI and laptops, which have no Substrate.
"""

import asyncio
import ipaddress
import os
import re
import ssl
import uuid
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import grpc
import httpx2
from google.protobuf import empty_pb2
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
ACCESS_POLICY_NAME = "default"  # every atespace has one policy, and Substrate fixes its name
EGRESS_POLICY_NAME = "default"  # so does every actor, for its egress policy


# What Substrate accepts as a hostname in an egress rule: a lowercase DNS-1123 subdomain. It refuses
# IP literals and trailing dots with INVALID_ARGUMENT, so a bad CONTEXT_API_URL would fail all jobs.
_HOSTNAME = re.compile(
    r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?(\.[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?)*$"
)


def _rule_hostname(url: str, hostname: str) -> str:
    """The URL's host as a rule's hostname; raise ValueError for what Substrate would refuse."""
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if len(hostname) <= 253 and _HOSTNAME.fullmatch(hostname):
            return hostname
    raise ValueError(
        f"CONTEXT_API_URL needs a DNS name without a trailing dot, not an IP address: {url!r}"
    )


def egress_rules(url: str) -> list[ate_pb2.EgressRule]:
    """The one destination an actor may reach, the Context API: its host and port, over its scheme.

    http is matched per request by the gateway, which dials the name itself. https is forwarded
    untouched, matched on the SNI. An empty URL means no Context API and so no destination.
    Raise ValueError for any other URL, and for a host Substrate refuses in a rule (an IP address,
    a trailing dot).
    """
    if not url:
        return []
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"CONTEXT_API_URL must be an http or https URL, got {url!r}")
    https = parts.scheme == "https"
    ports = ate_pb2.Ports(numbers=[parts.port or (443 if https else 80)])
    hostnames = [_rule_hostname(url, parts.hostname)]
    if https:
        return [
            ate_pb2.EgressRule(
                tls_passthrough=ate_pb2.TlsPassthroughRule(hostnames=hostnames, ports=ports)
            )
        ]
    return [ate_pb2.EgressRule(http=ate_pb2.HttpRule(hostnames=hostnames, ports=ports))]


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

    async def call[R: Message](self, method: str, request: Message, reply: type[R]) -> R:
        """Call ate-api with the current token; file and gRPC errors propagate."""
        token = self.token_file.read_text().strip()
        async with self.open_channel() as channel:
            call = channel.unary_unary(
                f"/ateapi.Control/{method}",
                request_serializer=type(request).SerializeToString,
                response_deserializer=reply.FromString,
            )
            return await call(
                request, metadata=[("authorization", f"Bearer {token}")], timeout=CONTROL_TIMEOUT
            )

    async def _call(self, method: str, request: Message) -> None:
        """Call and drop the reply: the dispatcher uses nothing in it."""
        await self.call(method, request, empty_pb2.Empty)

    async def create_actor(self, atespace: str, name: str, template: str) -> None:
        actor = ate_pb2.Actor(
            metadata=ate_pb2.ResourceMetadata(atespace=atespace, name=name),
            actor_template=ate_pb2.ObjectRef(atespace=atespace, name=template),
        )
        await self._call("CreateActor", ate_pb2.CreateActorRequest(actor=actor))

    async def create_egress_policy(
        self, atespace: str, actor: str, rules: list[ate_pb2.EgressRule]
    ) -> None:
        """Allow the actor these destinations and no others: without a policy it reaches nothing."""
        policy = ate_pb2.EgressPolicy(
            metadata=ate_pb2.ResourceMetadata(atespace=atespace, name=EGRESS_POLICY_NAME),
            rules=rules,
        )
        request = ate_pb2.CreateActorEgressPolicyRequest(
            actor=ate_pb2.ObjectRef(atespace=atespace, name=actor), egress_policy=policy
        )
        await self._call("CreateActorEgressPolicy", request)

    async def get_egress_policy(self, atespace: str, actor: str) -> ate_pb2.EgressPolicy:
        request = ate_pb2.GetActorEgressPolicyRequest(
            actor=ate_pb2.ObjectRef(atespace=atespace, name=actor), name=EGRESS_POLICY_NAME
        )
        return await self.call("GetActorEgressPolicy", request, ate_pb2.EgressPolicy)

    async def update_egress_policy(
        self, atespace: str, actor: str, policy: ate_pb2.EgressPolicy
    ) -> None:
        """Replace the policy. It must carry the uid and version it was read with."""
        request = ate_pb2.UpdateActorEgressPolicyRequest(
            actor=ate_pb2.ObjectRef(atespace=atespace, name=actor), egress_policy=policy
        )
        await self._call("UpdateActorEgressPolicy", request)

    async def suspend_actor(self, atespace: str, name: str) -> None:
        ref = ate_pb2.ObjectRef(atespace=atespace, name=name)
        await self._call("SuspendActor", ate_pb2.SuspendActorRequest(actor=ref))

    async def delete_actor(self, atespace: str, name: str) -> None:
        ref = ate_pb2.ObjectRef(atespace=atespace, name=name)
        await self._call("DeleteActor", ate_pb2.DeleteActorRequest(actor=ref))

    # Setup calls for substrate_sync.py. Unlike the dispatcher's, they need an owner identity.

    async def create_atespace(self, name: str) -> None:
        atespace = ate_pb2.Atespace(metadata=ate_pb2.ResourceMetadata(name=name))
        await self._call("CreateAtespace", ate_pb2.CreateAtespaceRequest(atespace=atespace))

    async def get_access_policy(self, atespace: str) -> ate_pb2.AccessPolicy:
        """Raise NOT_FOUND if the atespace has no policy (or no atespace)."""
        request = ate_pb2.GetAtespaceAccessPolicyRequest(
            atespace=ate_pb2.ObjectRef(name=atespace), name=ACCESS_POLICY_NAME
        )
        return await self.call("GetAtespaceAccessPolicy", request, ate_pb2.AccessPolicy)

    async def create_access_policy(self, atespace: str, policy: ate_pb2.AccessPolicy) -> None:
        request = ate_pb2.CreateAtespaceAccessPolicyRequest(
            atespace=ate_pb2.ObjectRef(name=atespace), access_policy=policy
        )
        await self._call("CreateAtespaceAccessPolicy", request)

    async def update_access_policy(self, atespace: str, policy: ate_pb2.AccessPolicy) -> None:
        request = ate_pb2.UpdateAtespaceAccessPolicyRequest(
            atespace=ate_pb2.ObjectRef(name=atespace), access_policy=policy
        )
        await self._call("UpdateAtespaceAccessPolicy", request)

    async def create_actor_template(self, template: ate_pb2.ActorTemplate) -> None:
        request = ate_pb2.CreateActorTemplateRequest(actor_template=template)
        await self._call("CreateActorTemplate", request)

    async def get_actor_template(self, atespace: str, name: str) -> ate_pb2.ActorTemplate:
        ref = ate_pb2.ObjectRef(atespace=atespace, name=name)
        request = ate_pb2.GetActorTemplateRequest(actor_template=ref)
        return await self.call("GetActorTemplate", request, ate_pb2.ActorTemplate)


def _actor_name(job_id: uuid.UUID) -> str:
    return f"job-{job_id}"


def _rule_set(rules: Iterable[ate_pb2.EgressRule]) -> list[bytes]:
    """Rules are an unordered set to Substrate, so compare them in a fixed order."""
    return sorted(rule.SerializeToString(deterministic=True) for rule in rules)


async def _created(call: Coroutine[Any, Any, None]) -> None:
    """Await a create call. The thing existing already (a later round, or a retry) is fine."""
    try:
        await call
    except grpc.aio.AioRpcError as error:
        if error.code() != grpc.StatusCode.ALREADY_EXISTS:
            raise


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
    egress: list[ate_pb2.EgressRule] = field(default_factory=list[ate_pb2.EgressRule])
    busy_wait: float = BUSY_WAIT

    async def solve(self, run: AgentRun) -> AgentAnswer:
        """Create or reuse the job's actor and send it a round through the router.

        An actor with no egress policy can reach nothing, so a policy for the Context API is created
        before the first request wakes the actor. Retry router 503 responses until busy_wait
        seconds have elapsed; other HTTP errors and actor or policy creation errors other than
        ALREADY_EXISTS propagate, as do transport and reply validation errors. A 503 at or after
        the deadline also propagates.
        """
        actor = _actor_name(run.job_id)
        await _created(self.control.create_actor(self.atespace, actor, self.template))
        if self.egress:  # a retry after a half-done create finds the actor but not its policy
            await self._set_egress(actor)
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

    async def _set_egress(self, actor: str) -> None:
        """Give the actor its policy, or bring the one it has up to date.

        An actor made before CONTEXT_API_URL changed keeps the old rules until a round comes in, so
        a policy that exists is compared with the wanted rules and replaced if they differ. The
        update carries the uid and version just read: if another request got there first, Substrate
        refuses it and the attempt fails and is retried.
        """
        try:
            await self.control.create_egress_policy(self.atespace, actor, self.egress)
            return
        except grpc.aio.AioRpcError as error:
            if error.code() != grpc.StatusCode.ALREADY_EXISTS:
                raise
        current = await self.control.get_egress_policy(self.atespace, actor)
        if _rule_set(current.rules) == _rule_set(self.egress):
            return
        del current.rules[:]
        current.rules.extend(self.egress)
        await self.control.update_egress_policy(self.atespace, actor, current)

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
        egress=egress_rules(os.environ.get("CONTEXT_API_URL", "")),
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
