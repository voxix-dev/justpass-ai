"""Brings what ate-api holds for the agents in line with deploy/substrate/agents.yaml.

    uv run python -m api.substrate_sync deploy/substrate/agents.yaml \\
        --agent-image <image@sha256:...>

Adds what is missing and prints only the template's name on stdout, for ATE_TEMPLATE. Running it
again with the same file and image changes nothing and prints the same name. Needs ate-api's
address, its CA and a token: --target, --ca-file, --token-file, or ATE_API_TARGET, ATE_CA_FILE and
ATE_TOKEN_FILE (the settings the API itself uses, see agents.py).

RIGHTS. The token must belong to an identity that may create atespaces (global owner) and write
the atespace's access policy (atespace owner, which a global owner also is). The API's own identity
is only an editor of its atespace, by design, so it can't do this. Locally deploy.sh mints a token
for ate-client, which is a bootstrap owner of the kind cluster. In production this runs in CI as an
identity listed in ate-api's bootstrap owners (--authz-bootstrap-owners); that is not built yet.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, Protocol

import grpc
import yaml
from google.protobuf import json_format
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from api import ate_pb2
from api.agents import ACCESS_POLICY_NAME, Control

log = logging.getLogger("substrate_sync")

GOLDEN_TIMEOUT = 240.0  # building the golden snapshot boots the agent once
GOLDEN_POLL = 2.0
PINNED_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


class Spec(BaseModel):
    """Base of the models of agents.yaml: camelCase like the protojson Substrate documents."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True
    )


class Binding(Spec):
    role: str
    members: list[str]


class AccessPolicySpec(Spec):
    bindings: list[Binding]


class Selector(Spec):
    match_labels: dict[str, str]


class HttpGet(Spec):
    path: str
    port: int


class WakeupProbe(Spec):
    http_get: HttpGet


class ContainerSpec(Spec):
    """The agent's container; the image comes from the command line, not the file."""

    name: str
    wakeup_probe: WakeupProbe


class Limits(Spec):
    cpu_millicores: int = Field(gt=0)
    memory_bytes: int = Field(gt=0)


class ResourceRequirements(Spec):
    limits: Limits


class SnapshotConfig(Spec):
    preferred_fidelity: Literal[
        "SNAPSHOT_FIDELITY_VOLUMES", "SNAPSHOT_FIDELITY_ROOTFS", "SNAPSHOT_FIDELITY_MEMORY"
    ]


class TemplateSpec(Spec):
    """An actor template without its name and image."""

    worker_selector: Selector
    container: ContainerSpec
    resources: ResourceRequirements
    snapshot_config: SnapshotConfig


class AgentsSpec(Spec):
    atespace: str
    access_policy: AccessPolicySpec
    template: TemplateSpec


def load_spec(path: Path) -> AgentsSpec:
    """Read agents.yaml; raise pydantic.ValidationError if it is not what we expect."""
    return AgentsSpec.model_validate(yaml.safe_load(path.read_text()))


def pinned_image(value: str) -> str:
    """Return the image if it is pinned by digest, as Substrate demands; raise ValueError if not."""
    if not PINNED_IMAGE.match(value):
        raise ValueError(f"agent image must be pinned by digest (name@sha256:<64 hex>): {value!r}")
    return value


def _template_dict(spec: TemplateSpec, image: str) -> dict[str, object]:
    """The template in protojson form, without its name."""
    body = spec.model_dump(mode="json", by_alias=True)
    container = body.pop("container")
    return {**body, "containers": [{**container, "image": pinned_image(image)}]}


def template_name(spec: TemplateSpec, image: str) -> str:
    """Name a template after all of its content, so an image or size change makes a new one."""
    canonical = json.dumps(_template_dict(spec, image), sort_keys=True, separators=(",", ":"))
    return f"agent-{hashlib.sha256(canonical.encode()).hexdigest()[:12]}"


def build_template(spec: AgentsSpec, image: str) -> ate_pb2.ActorTemplate:
    body = {
        "metadata": {"atespace": spec.atespace, "name": template_name(spec.template, image)},
        **_template_dict(spec.template, image),
    }
    return json_format.ParseDict(body, ate_pb2.ActorTemplate())


def build_policy(spec: AgentsSpec) -> ate_pb2.AccessPolicy:
    # The atespace is the policy's parent, so Substrate wants it out of the policy's own metadata.
    return ate_pb2.AccessPolicy(
        metadata=ate_pb2.ResourceMetadata(name=ACCESS_POLICY_NAME),
        bindings=[
            ate_pb2.Binding(role=b.role, members=b.members) for b in spec.access_policy.bindings
        ],
    )


def _bindings(policy: ate_pb2.AccessPolicy) -> list[tuple[str, tuple[str, ...]]]:
    """Bindings as Substrate compares them: members are a set, bindings are a list."""
    return [(b.role, tuple(sorted(b.members))) for b in policy.bindings]


class Admin(Protocol):
    """The ate-api calls this module makes; Control implements them."""

    async def create_atespace(self, name: str) -> None: ...
    async def get_access_policy(self, atespace: str) -> ate_pb2.AccessPolicy: ...
    async def create_access_policy(self, atespace: str, policy: ate_pb2.AccessPolicy) -> None: ...
    async def update_access_policy(self, atespace: str, policy: ate_pb2.AccessPolicy) -> None: ...
    async def create_actor_template(self, template: ate_pb2.ActorTemplate) -> None: ...
    async def get_actor_template(self, atespace: str, name: str) -> ate_pb2.ActorTemplate: ...


class GoldenSnapshotFailed(Exception):
    """The template controller gave up on the template's golden snapshot."""


def _code(error: grpc.aio.AioRpcError) -> grpc.StatusCode:
    return error.code()  # pyright: ignore[reportReturnType]


async def _create_if_absent(create: Awaitable[None]) -> bool:
    """Run a create call; return False if the thing already existed, raise on other errors."""
    try:
        await create
    except grpc.aio.AioRpcError as error:
        if _code(error) != grpc.StatusCode.ALREADY_EXISTS:
            raise
        return False
    return True


async def ensure_policy(admin: Admin, spec: AgentsSpec) -> None:
    """Create the atespace's access policy, or replace it if it differs from the file.

    The file is the truth, so a policy changed by hand is put back. The update carries the stored
    uid and version as preconditions; if someone changed the policy since we read it, ate-api
    answers ABORTED and this fails loudly instead of overwriting a newer change.
    """
    wanted = build_policy(spec)
    try:
        current = await admin.get_access_policy(spec.atespace)
    except grpc.aio.AioRpcError as error:
        if _code(error) != grpc.StatusCode.NOT_FOUND:
            raise
        await admin.create_access_policy(spec.atespace, wanted)
        log.info("created the access policy of %s", spec.atespace)
        return
    if _bindings(current) == _bindings(wanted):
        return
    wanted.metadata.uid = current.metadata.uid
    wanted.metadata.version = current.metadata.version
    await admin.update_access_policy(spec.atespace, wanted)
    log.info("updated the access policy of %s", spec.atespace)


async def wait_for_golden(
    admin: Admin,
    atespace: str,
    name: str,
    timeout: float = GOLDEN_TIMEOUT,
    poll: float = GOLDEN_POLL,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Return once the template's golden snapshot exists.

    Raises GoldenSnapshotFailed if the controller reports an error, and TimeoutError after
    `timeout` seconds. A failed read is retried until then, and named in the timeout.
    """
    deadline = clock() + timeout
    last_error: grpc.aio.AioRpcError | None = None
    while True:
        try:
            template = await admin.get_actor_template(atespace, name)
        except grpc.aio.AioRpcError as error:
            last_error = error
        else:
            golden = template.status.golden_snapshot_status
            if golden.golden_tag.name:
                return
            if golden.error_message:
                raise GoldenSnapshotFailed(f"{name}: {golden.error_message}")
        if clock() >= deadline:
            raise TimeoutError(
                f"no golden snapshot for {name} after {timeout:.0f}s"
                + (f", last error: {last_error}" if last_error else "")
            )
        await sleep(poll)


async def sync(
    admin: Admin, spec: AgentsSpec, image: str, golden_timeout: float = GOLDEN_TIMEOUT
) -> str:
    """Make ate-api match the spec and return the template's name, once it can start actors."""
    template = build_template(spec, image)
    name = template.metadata.name
    if await _create_if_absent(admin.create_atespace(spec.atespace)):
        log.info("created the atespace %s", spec.atespace)
    await ensure_policy(admin, spec)
    if await _create_if_absent(admin.create_actor_template(template)):
        log.info("created the actor template %s", name)
    log.info("waiting for the golden snapshot of %s", name)
    await wait_for_golden(admin, spec.atespace, name, timeout=golden_timeout)
    return name


def _control(target: str, ca_file: Path, token_file: Path, server_name: str | None) -> Control:
    # A port-forward reaches ate-api as 127.0.0.1, but its certificate names the service.
    options = [("grpc.ssl_target_name_override", server_name)] if server_name else None

    def open_channel() -> grpc.aio.Channel:
        credentials = grpc.ssl_channel_credentials(ca_file.read_bytes())
        return grpc.aio.secure_channel(target, credentials, options=options)

    return Control(open_channel, token_file)


def _parser() -> argparse.ArgumentParser:
    def image(value: str) -> str:
        try:
            return pinned_image(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError(str(error)) from error

    env = os.environ.get
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("spec", type=Path, help="agents.yaml")
    parser.add_argument("--agent-image", type=image, required=True, help="name@sha256:<digest>")
    parser.add_argument("--target", default=env("ATE_API_TARGET"), help="host:port of ate-api")
    parser.add_argument("--ca-file", type=Path, default=env("ATE_CA_FILE"), help="CA of ate-api")
    parser.add_argument("--token-file", type=Path, default=env("ATE_TOKEN_FILE"), help="token")
    parser.add_argument(
        "--server-name",
        default=env("ATE_API_SERVER_NAME"),
        help="name on ate-api's certificate, if --target is not that name (ATE_API_SERVER_NAME)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    parser = _parser()
    args = parser.parse_args(argv)
    for flag in ("target", "ca_file", "token_file"):
        if getattr(args, flag) is None:
            parser.error(f"--{flag.replace('_', '-')} or its ATE_* setting is required")
    control = _control(args.target, args.ca_file, args.token_file, args.server_name)
    print(asyncio.run(sync(control, load_spec(args.spec), args.agent_image)))


if __name__ == "__main__":
    main()
