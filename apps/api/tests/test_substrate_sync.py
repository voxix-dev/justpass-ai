import re
from dataclasses import dataclass, field
from pathlib import Path

import grpc
import pytest
import yaml
from pydantic import ValidationError

from api import ate_pb2
from api.agents import ACCESS_POLICY_NAME, Control
from api.substrate_sync import (
    AgentsSpec,
    GoldenSnapshotFailed,
    build_policy,
    build_template,
    ensure_policy,
    load_spec,
    main,
    pinned_image,
    sync,
    template_name,
    wait_for_golden,
)

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parents[3]
SUBSTRATE = ROOT / "deploy" / "substrate"
IMAGE = "localhost:5001/agent@sha256:" + "a" * 64
OTHER_IMAGE = "localhost:5001/agent@sha256:" + "b" * 64


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def spec() -> AgentsSpec:
    return load_spec(SUBSTRATE / "agents.yaml")


def rpc_error(code: grpc.StatusCode) -> grpc.aio.AioRpcError:
    return grpc.aio.AioRpcError(code, grpc.aio.Metadata(), grpc.aio.Metadata(), details="x")


@dataclass
class FakeAdmin:
    """ate-api in memory: what it holds, and the calls that changed it."""

    atespaces: set[str] = field(default_factory=set[str])
    policy: ate_pb2.AccessPolicy | None = None
    templates: dict[str, ate_pb2.ActorTemplate] = field(
        default_factory=dict[str, ate_pb2.ActorTemplate]
    )
    calls: list[str] = field(default_factory=list[str])
    golden_after: int = 0  # reads before the golden snapshot shows up
    golden_error: str = ""
    read_errors: list[grpc.StatusCode] = field(default_factory=list[grpc.StatusCode])
    reads: int = 0

    async def create_atespace(self, name: str) -> None:
        self.calls.append("create_atespace")
        if name in self.atespaces:
            raise rpc_error(grpc.StatusCode.ALREADY_EXISTS)
        self.atespaces.add(name)

    async def get_access_policy(self, atespace: str) -> ate_pb2.AccessPolicy:
        if self.policy is None:
            raise rpc_error(grpc.StatusCode.NOT_FOUND)
        return self.policy

    async def create_access_policy(self, atespace: str, policy: ate_pb2.AccessPolicy) -> None:
        self.calls.append("create_access_policy")
        self.policy = policy
        self.policy.metadata.uid = "uid-1"
        self.policy.metadata.version = 1

    async def update_access_policy(self, atespace: str, policy: ate_pb2.AccessPolicy) -> None:
        self.calls.append("update_access_policy")
        assert self.policy is not None
        assert (policy.metadata.uid, policy.metadata.version) == (
            self.policy.metadata.uid,
            self.policy.metadata.version,
        )
        self.policy = policy
        self.policy.metadata.version += 1

    async def create_actor_template(self, template: ate_pb2.ActorTemplate) -> None:
        self.calls.append("create_actor_template")
        if template.metadata.name in self.templates:
            raise rpc_error(grpc.StatusCode.ALREADY_EXISTS)
        self.templates[template.metadata.name] = template

    async def get_actor_template(self, atespace: str, name: str) -> ate_pb2.ActorTemplate:
        self.reads += 1
        if self.read_errors:
            raise rpc_error(self.read_errors.pop(0))
        template = ate_pb2.ActorTemplate()
        template.CopyFrom(self.templates[name])
        status = template.status.golden_snapshot_status
        if self.golden_error:
            status.error_message = self.golden_error
        elif self.reads > self.golden_after:
            status.golden_tag.name = f"golden-{name}"
        return template


class Clock:
    """Time that only moves when a wait sleeps, so waits finish at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


async def wait(admin: FakeAdmin, spec: AgentsSpec, timeout: float = 20.0) -> None:
    clock = Clock()
    name = template_name(spec.template, IMAGE)
    await wait_for_golden(
        admin, spec.atespace, name, timeout=timeout, poll=2.0, sleep=clock.sleep, clock=clock
    )


def test_agents_yaml_becomes_the_template_ate_api_expects(spec: AgentsSpec):
    template = build_template(spec, IMAGE)

    assert (template.metadata.atespace, template.metadata.name) == (
        "justpass-agents",
        template_name(spec.template, IMAGE),
    )
    assert dict(template.worker_selector.match_labels) == {"workload": "justpass-agent"}
    [container] = template.containers
    assert (container.name, container.image) == ("agent", IMAGE)
    assert (container.wakeup_probe.http_get.path, container.wakeup_probe.http_get.port) == (
        "/readyz",
        80,
    )
    assert template.resources.limits.cpu_millicores == 1000
    assert template.resources.limits.memory_bytes == 512 * 1024 * 1024
    assert template.snapshot_config.preferred_fidelity == ate_pb2.SNAPSHOT_FIDELITY_MEMORY


def test_agents_yaml_becomes_the_policy_ate_api_expects(spec: AgentsSpec):
    policy = build_policy(spec)

    assert (policy.metadata.name, policy.metadata.atespace) == (ACCESS_POLICY_NAME, "")
    [binding] = policy.bindings
    assert binding.role == "editor"
    assert list(binding.members) == ["user:system:serviceaccount:homework:api"]


def test_a_misspelled_or_missing_field_is_refused():
    raw = yaml.safe_load((SUBSTRATE / "agents.yaml").read_text())
    with pytest.raises(ValidationError):
        AgentsSpec.model_validate({**raw, "atespaces": "x"})
    del raw["template"]["resources"]
    with pytest.raises(ValidationError):
        AgentsSpec.model_validate(raw)


def test_a_bad_size_is_refused():
    raw = yaml.safe_load((SUBSTRATE / "agents.yaml").read_text())
    raw["template"]["resources"]["limits"]["cpuMillicores"] = 0
    with pytest.raises(ValidationError):
        AgentsSpec.model_validate(raw)


def test_template_name_is_stable_and_follows_the_content(spec: AgentsSpec):
    name = template_name(spec.template, IMAGE)
    assert re.fullmatch(r"agent-[0-9a-f]{12}", name)
    assert template_name(load_spec(SUBSTRATE / "agents.yaml").template, IMAGE) == name

    assert template_name(spec.template, OTHER_IMAGE) != name

    limits = spec.template.resources.limits
    bigger = spec.template.model_copy(
        update={
            "resources": spec.template.resources.model_copy(
                update={
                    "limits": limits.model_copy(update={"memory_bytes": limits.memory_bytes * 2})
                }
            )
        }
    )
    assert template_name(bigger, IMAGE) != name


@pytest.mark.parametrize(
    "image",
    ["agent:dev", "agent@sha256:abc", "agent@sha256:" + "A" * 64, "", "a b@sha256:" + "a" * 64],
)
def test_an_image_not_pinned_by_digest_is_refused(image: str, spec: AgentsSpec):
    with pytest.raises(ValueError, match="pinned by digest"):
        pinned_image(image)
    with pytest.raises(ValueError, match="pinned by digest"):
        template_name(spec.template, image)


def test_the_command_refuses_an_unpinned_image(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit):
        main([str(SUBSTRATE / "agents.yaml"), "--agent-image", "agent:dev"])
    assert "pinned by digest" in capsys.readouterr().err


async def test_a_first_sync_creates_everything_and_returns_the_template_name(spec: AgentsSpec):
    admin = FakeAdmin()

    name = await sync(admin, spec, IMAGE)

    assert name == template_name(spec.template, IMAGE)
    assert admin.calls == [
        "create_atespace",
        "create_access_policy",
        "create_actor_template",
    ]


async def test_a_second_sync_changes_nothing_and_gives_the_same_name(spec: AgentsSpec):
    admin = FakeAdmin()
    first = await sync(admin, spec, IMAGE)
    admin.calls.clear()

    assert await sync(admin, spec, IMAGE) == first
    # The atespace and the template answer ALREADY_EXISTS, which is fine; the policy is left alone.
    assert admin.calls == ["create_atespace", "create_actor_template"]


async def test_another_image_makes_a_second_template(spec: AgentsSpec):
    admin = FakeAdmin()
    first = await sync(admin, spec, IMAGE)
    second = await sync(admin, spec, OTHER_IMAGE)

    assert first != second
    assert set(admin.templates) == {first, second}


async def test_other_errors_are_not_taken_for_already_exists(spec: AgentsSpec):
    class Denied(FakeAdmin):
        async def create_atespace(self, name: str) -> None:
            raise rpc_error(grpc.StatusCode.PERMISSION_DENIED)

    with pytest.raises(grpc.aio.AioRpcError) as error:
        await sync(Denied(), spec, IMAGE)
    assert error.value.code() == grpc.StatusCode.PERMISSION_DENIED


async def test_a_policy_that_differs_is_replaced_with_the_stored_uid_and_version(
    spec: AgentsSpec,
):
    admin = FakeAdmin()
    stored = ate_pb2.AccessPolicy(
        metadata=ate_pb2.ResourceMetadata(name=ACCESS_POLICY_NAME, uid="u", version=7),
        bindings=[ate_pb2.Binding(role="owner", members=["user:someone"])],
    )
    admin.policy = stored

    await ensure_policy(admin, spec)

    assert admin.calls == ["update_access_policy"]  # its asserts check uid and version
    assert admin.policy is not None
    assert [(b.role, list(b.members)) for b in admin.policy.bindings] == [
        ("editor", ["user:system:serviceaccount:homework:api"])
    ]


async def test_a_policy_that_only_orders_members_differently_is_left_alone(spec: AgentsSpec):
    stored = build_policy(spec)
    stored.bindings[0].members.insert(0, "user:b")
    admin = FakeAdmin(policy=stored)
    binding = spec.access_policy.bindings[0]
    members = ["user:b", *binding.members]
    reordered = [binding.model_copy(update={"members": [*reversed(members)]})]
    wanted = spec.model_copy(
        update={"access_policy": spec.access_policy.model_copy(update={"bindings": reordered})}
    )

    await ensure_policy(admin, wanted)

    assert admin.calls == []


async def test_a_policy_changed_meanwhile_fails_instead_of_overwriting(spec: AgentsSpec):
    class Raced(FakeAdmin):
        async def update_access_policy(self, atespace: str, policy: ate_pb2.AccessPolicy) -> None:
            raise rpc_error(grpc.StatusCode.ABORTED)

    admin = Raced()
    admin.policy = ate_pb2.AccessPolicy(metadata=ate_pb2.ResourceMetadata(uid="u", version=1))
    with pytest.raises(grpc.aio.AioRpcError) as error:
        await ensure_policy(admin, spec)
    assert error.value.code() == grpc.StatusCode.ABORTED


async def test_wait_returns_when_the_golden_snapshot_appears(spec: AgentsSpec):
    admin = FakeAdmin(golden_after=3)
    await admin.create_actor_template(build_template(spec, IMAGE))

    await wait(admin, spec)

    assert admin.reads == 4


async def test_wait_fails_at_once_on_a_template_error(spec: AgentsSpec):
    admin = FakeAdmin(golden_error="ImagePullFailed: no such image")
    await admin.create_actor_template(build_template(spec, IMAGE))

    with pytest.raises(GoldenSnapshotFailed, match="ImagePullFailed"):
        await wait(admin, spec)

    assert admin.reads == 1


async def test_wait_times_out_and_names_the_last_read_error(spec: AgentsSpec):
    admin = FakeAdmin(golden_after=10**6)
    await admin.create_actor_template(build_template(spec, IMAGE))
    with pytest.raises(TimeoutError, match="no golden snapshot"):
        await wait(admin, spec, timeout=10)

    admin = FakeAdmin()
    admin.read_errors = [grpc.StatusCode.UNAVAILABLE] * 100
    await admin.create_actor_template(build_template(spec, IMAGE))
    with pytest.raises(TimeoutError, match="UNAVAILABLE"):
        await wait(admin, spec, timeout=10)


async def test_fake_admin_stands_in_for_control(spec: AgentsSpec, tmp_path: Path):
    """Control must have every call that sync makes."""
    control = Control(lambda: grpc.aio.insecure_channel("127.0.0.1:1"), tmp_path / "token")
    for method in vars(FakeAdmin):
        if method.startswith(("create_", "get_", "update_")):
            assert hasattr(control, method), method


QUANTITY = re.compile(r"^(\d+)(m|Ki|Mi|Gi)?$")
SUFFIX = {None: 1, "m": 0.001, "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}


def quantity(value: str | int) -> float:
    """A Kubernetes quantity as a number: cores for cpu, bytes for memory."""
    match = QUANTITY.match(str(value))
    assert match, f"unsupported quantity {value!r}"
    return int(match[1]) * SUFFIX[match[2]]


def test_quantity_reads_what_the_worker_pool_may_say():
    assert quantity("1000m") == 1
    assert quantity("1") == 1
    assert quantity("250m") == 0.25
    assert quantity("1Gi") == 1024**3
    assert quantity("512Mi") == 512 * 1024**2
    assert quantity(2048) == 2048


def test_the_sandbox_fits_inside_its_worker(spec: AgentsSpec):
    """Keeps agent-pool.yaml and agents.yaml from drifting apart: the sandbox runs in the worker."""
    documents = yaml.safe_load_all((SUBSTRATE / "agent-pool.yaml").read_text())
    [pool] = [d for d in documents if d["kind"] == "WorkerPool"]
    worker = pool["spec"]["template"]["resources"]["limits"]
    sandbox = spec.template.resources.limits

    assert sandbox.cpu_millicores <= quantity(worker["cpu"]) * 1000
    assert sandbox.memory_bytes <= quantity(worker["memory"])
    assert pool["metadata"]["labels"].items() >= spec.template.worker_selector.match_labels.items()
