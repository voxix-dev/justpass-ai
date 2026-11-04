"""ate.proto is a subset of Substrate's ateapi.proto: whatever it keeps must match upstream."""

import os
import urllib.request
from pathlib import Path

import pytest
from google.protobuf import descriptor_pb2 as pb
from grpc_tools import protoc

# Bump to follow upstream, then fix whatever this test reports.
UPSTREAM_COMMIT = "52386bd54c6965deeebac1b1a6b6de40e9c1c33a"
UPSTREAM_URL = (
    "https://raw.githubusercontent.com/agent-substrate/substrate/"
    f"{UPSTREAM_COMMIT}/pkg/proto/ateapipb/ateapi.proto"
)
OURS = Path(__file__).parents[1] / "src/api/ate.proto"

type Field = tuple[str, int, str, int, bool]  # (name, type, type_name, label, proto3_optional)


def describe(proto: Path, out: Path) -> pb.FileDescriptorSet:
    out.parent.mkdir(exist_ok=True)
    include = Path(protoc.__file__).parent / "_proto"  # google/protobuf/*.proto
    args = [
        "protoc",
        f"-I{proto.parent}",
        f"-I{include}",
        f"--descriptor_set_out={out}",
        str(proto),
    ]
    assert protoc.main(args) == 0
    return pb.FileDescriptorSet.FromString(out.read_bytes())


def messages_and_enums(
    fds: pb.FileDescriptorSet,
) -> tuple[dict[str, dict[int, Field]], dict[str, dict[int, str]]]:
    """Messages as {full name: {field number: details}}, enums as {full name: {number: name}}."""
    messages: dict[str, dict[int, Field]] = {}
    enums: dict[str, dict[int, str]] = {}

    def walk_enum(prefix: str, enum: pb.EnumDescriptorProto) -> None:
        enums[f"{prefix}.{enum.name}"] = {v.number: v.name for v in enum.value}

    def walk(prefix: str, msg: pb.DescriptorProto) -> None:
        name = f"{prefix}.{msg.name}"
        messages[name] = {
            f.number: (f.name, f.type, f.type_name, f.label, f.proto3_optional) for f in msg.field
        }
        for nested in msg.nested_type:
            walk(name, nested)
        for enum in msg.enum_type:
            walk_enum(name, enum)

    for file in fds.file:
        for msg in file.message_type:
            walk(file.package, msg)
        for enum in file.enum_type:
            walk_enum(file.package, enum)
    return messages, enums


def upstream_proto(tmp_path: Path) -> Path:
    try:
        with urllib.request.urlopen(UPSTREAM_URL, timeout=30) as response:
            body = response.read()
    except OSError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"cannot download upstream ateapi.proto: {exc}")
    path = tmp_path / "upstream/ateapi.proto"
    path.parent.mkdir()
    path.write_bytes(body)
    return path


def test_ate_proto_matches_upstream(tmp_path: Path) -> None:
    ours_messages, ours_enums = messages_and_enums(describe(OURS, tmp_path / "ours.pb"))
    up_messages, up_enums = messages_and_enums(
        describe(upstream_proto(tmp_path), tmp_path / "up.pb")
    )

    problems: list[str] = []
    for kind, ours, upstream in [
        ("field", ours_messages, up_messages),
        ("value", ours_enums, up_enums),
    ]:
        for name, items in ours.items():
            for number, item in items.items():
                theirs = upstream.get(name, {}).get(number)
                if theirs != item:
                    problems.append(f"{name} {kind} {number}: ours {item}, upstream {theirs}")
    assert not problems, "\n".join(problems)
