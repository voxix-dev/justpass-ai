from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SnapshotFidelity(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SNAPSHOT_FIDELITY_UNSPECIFIED: _ClassVar[SnapshotFidelity]
    SNAPSHOT_FIDELITY_VOLUMES: _ClassVar[SnapshotFidelity]
    SNAPSHOT_FIDELITY_ROOTFS: _ClassVar[SnapshotFidelity]
    SNAPSHOT_FIDELITY_MEMORY: _ClassVar[SnapshotFidelity]
SNAPSHOT_FIDELITY_UNSPECIFIED: SnapshotFidelity
SNAPSHOT_FIDELITY_VOLUMES: SnapshotFidelity
SNAPSHOT_FIDELITY_ROOTFS: SnapshotFidelity
SNAPSHOT_FIDELITY_MEMORY: SnapshotFidelity

class ObjectRef(_message.Message):
    __slots__ = ("atespace", "name")
    ATESPACE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    atespace: str
    name: str
    def __init__(self, atespace: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class ResourceMetadata(_message.Message):
    __slots__ = ("atespace", "name", "uid", "version")
    ATESPACE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    UID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    atespace: str
    name: str
    uid: str
    version: int
    def __init__(self, atespace: _Optional[str] = ..., name: _Optional[str] = ..., uid: _Optional[str] = ..., version: _Optional[int] = ...) -> None: ...

class Actor(_message.Message):
    __slots__ = ("metadata", "actor_template")
    METADATA_FIELD_NUMBER: _ClassVar[int]
    ACTOR_TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    metadata: ResourceMetadata
    actor_template: ObjectRef
    def __init__(self, metadata: _Optional[_Union[ResourceMetadata, _Mapping]] = ..., actor_template: _Optional[_Union[ObjectRef, _Mapping]] = ...) -> None: ...

class CreateActorRequest(_message.Message):
    __slots__ = ("actor",)
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    actor: Actor
    def __init__(self, actor: _Optional[_Union[Actor, _Mapping]] = ...) -> None: ...

class SuspendActorRequest(_message.Message):
    __slots__ = ("actor",)
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    actor: ObjectRef
    def __init__(self, actor: _Optional[_Union[ObjectRef, _Mapping]] = ...) -> None: ...

class DeleteActorRequest(_message.Message):
    __slots__ = ("actor",)
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    actor: ObjectRef
    def __init__(self, actor: _Optional[_Union[ObjectRef, _Mapping]] = ...) -> None: ...

class Atespace(_message.Message):
    __slots__ = ("metadata",)
    METADATA_FIELD_NUMBER: _ClassVar[int]
    metadata: ResourceMetadata
    def __init__(self, metadata: _Optional[_Union[ResourceMetadata, _Mapping]] = ...) -> None: ...

class CreateAtespaceRequest(_message.Message):
    __slots__ = ("atespace",)
    ATESPACE_FIELD_NUMBER: _ClassVar[int]
    atespace: Atespace
    def __init__(self, atespace: _Optional[_Union[Atespace, _Mapping]] = ...) -> None: ...

class Selector(_message.Message):
    __slots__ = ("match_labels",)
    class MatchLabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    MATCH_LABELS_FIELD_NUMBER: _ClassVar[int]
    match_labels: _containers.ScalarMap[str, str]
    def __init__(self, match_labels: _Optional[_Mapping[str, str]] = ...) -> None: ...

class HttpGetAction(_message.Message):
    __slots__ = ("path", "port")
    PATH_FIELD_NUMBER: _ClassVar[int]
    PORT_FIELD_NUMBER: _ClassVar[int]
    path: str
    port: int
    def __init__(self, path: _Optional[str] = ..., port: _Optional[int] = ...) -> None: ...

class WakeupProbe(_message.Message):
    __slots__ = ("http_get", "timeout_seconds")
    HTTP_GET_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    http_get: HttpGetAction
    timeout_seconds: int
    def __init__(self, http_get: _Optional[_Union[HttpGetAction, _Mapping]] = ..., timeout_seconds: _Optional[int] = ...) -> None: ...

class Container(_message.Message):
    __slots__ = ("name", "image", "wakeup_probe")
    NAME_FIELD_NUMBER: _ClassVar[int]
    IMAGE_FIELD_NUMBER: _ClassVar[int]
    WAKEUP_PROBE_FIELD_NUMBER: _ClassVar[int]
    name: str
    image: str
    wakeup_probe: WakeupProbe
    def __init__(self, name: _Optional[str] = ..., image: _Optional[str] = ..., wakeup_probe: _Optional[_Union[WakeupProbe, _Mapping]] = ...) -> None: ...

class Resources(_message.Message):
    __slots__ = ("cpu_millicores", "memory_bytes")
    CPU_MILLICORES_FIELD_NUMBER: _ClassVar[int]
    MEMORY_BYTES_FIELD_NUMBER: _ClassVar[int]
    cpu_millicores: int
    memory_bytes: int
    def __init__(self, cpu_millicores: _Optional[int] = ..., memory_bytes: _Optional[int] = ...) -> None: ...

class ResourceRequirements(_message.Message):
    __slots__ = ("limits",)
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    limits: Resources
    def __init__(self, limits: _Optional[_Union[Resources, _Mapping]] = ...) -> None: ...

class SnapshotConfig(_message.Message):
    __slots__ = ("preferred_fidelity",)
    PREFERRED_FIDELITY_FIELD_NUMBER: _ClassVar[int]
    preferred_fidelity: SnapshotFidelity
    def __init__(self, preferred_fidelity: _Optional[_Union[SnapshotFidelity, str]] = ...) -> None: ...

class GoldenSnapshotStatus(_message.Message):
    __slots__ = ("golden_tag", "error_message")
    GOLDEN_TAG_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    golden_tag: ObjectRef
    error_message: str
    def __init__(self, golden_tag: _Optional[_Union[ObjectRef, _Mapping]] = ..., error_message: _Optional[str] = ...) -> None: ...

class ActorTemplateStatus(_message.Message):
    __slots__ = ("golden_snapshot_status",)
    GOLDEN_SNAPSHOT_STATUS_FIELD_NUMBER: _ClassVar[int]
    golden_snapshot_status: GoldenSnapshotStatus
    def __init__(self, golden_snapshot_status: _Optional[_Union[GoldenSnapshotStatus, _Mapping]] = ...) -> None: ...

class ActorTemplate(_message.Message):
    __slots__ = ("metadata", "worker_selector", "containers", "snapshot_config", "resources", "status")
    METADATA_FIELD_NUMBER: _ClassVar[int]
    WORKER_SELECTOR_FIELD_NUMBER: _ClassVar[int]
    CONTAINERS_FIELD_NUMBER: _ClassVar[int]
    SNAPSHOT_CONFIG_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    metadata: ResourceMetadata
    worker_selector: Selector
    containers: _containers.RepeatedCompositeFieldContainer[Container]
    snapshot_config: SnapshotConfig
    resources: ResourceRequirements
    status: ActorTemplateStatus
    def __init__(self, metadata: _Optional[_Union[ResourceMetadata, _Mapping]] = ..., worker_selector: _Optional[_Union[Selector, _Mapping]] = ..., containers: _Optional[_Iterable[_Union[Container, _Mapping]]] = ..., snapshot_config: _Optional[_Union[SnapshotConfig, _Mapping]] = ..., resources: _Optional[_Union[ResourceRequirements, _Mapping]] = ..., status: _Optional[_Union[ActorTemplateStatus, _Mapping]] = ...) -> None: ...

class CreateActorTemplateRequest(_message.Message):
    __slots__ = ("actor_template",)
    ACTOR_TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    actor_template: ActorTemplate
    def __init__(self, actor_template: _Optional[_Union[ActorTemplate, _Mapping]] = ...) -> None: ...

class GetActorTemplateRequest(_message.Message):
    __slots__ = ("actor_template",)
    ACTOR_TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    actor_template: ObjectRef
    def __init__(self, actor_template: _Optional[_Union[ObjectRef, _Mapping]] = ...) -> None: ...

class AccessPolicy(_message.Message):
    __slots__ = ("metadata", "bindings")
    METADATA_FIELD_NUMBER: _ClassVar[int]
    BINDINGS_FIELD_NUMBER: _ClassVar[int]
    metadata: ResourceMetadata
    bindings: _containers.RepeatedCompositeFieldContainer[Binding]
    def __init__(self, metadata: _Optional[_Union[ResourceMetadata, _Mapping]] = ..., bindings: _Optional[_Iterable[_Union[Binding, _Mapping]]] = ...) -> None: ...

class Binding(_message.Message):
    __slots__ = ("role", "members")
    ROLE_FIELD_NUMBER: _ClassVar[int]
    MEMBERS_FIELD_NUMBER: _ClassVar[int]
    role: str
    members: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, role: _Optional[str] = ..., members: _Optional[_Iterable[str]] = ...) -> None: ...

class GetAtespaceAccessPolicyRequest(_message.Message):
    __slots__ = ("atespace", "name")
    ATESPACE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    atespace: ObjectRef
    name: str
    def __init__(self, atespace: _Optional[_Union[ObjectRef, _Mapping]] = ..., name: _Optional[str] = ...) -> None: ...

class CreateAtespaceAccessPolicyRequest(_message.Message):
    __slots__ = ("atespace", "access_policy")
    ATESPACE_FIELD_NUMBER: _ClassVar[int]
    ACCESS_POLICY_FIELD_NUMBER: _ClassVar[int]
    atespace: ObjectRef
    access_policy: AccessPolicy
    def __init__(self, atespace: _Optional[_Union[ObjectRef, _Mapping]] = ..., access_policy: _Optional[_Union[AccessPolicy, _Mapping]] = ...) -> None: ...

class UpdateAtespaceAccessPolicyRequest(_message.Message):
    __slots__ = ("atespace", "access_policy")
    ATESPACE_FIELD_NUMBER: _ClassVar[int]
    ACCESS_POLICY_FIELD_NUMBER: _ClassVar[int]
    atespace: ObjectRef
    access_policy: AccessPolicy
    def __init__(self, atespace: _Optional[_Union[ObjectRef, _Mapping]] = ..., access_policy: _Optional[_Union[AccessPolicy, _Mapping]] = ...) -> None: ...

class EgressPolicy(_message.Message):
    __slots__ = ("metadata", "rules")
    METADATA_FIELD_NUMBER: _ClassVar[int]
    RULES_FIELD_NUMBER: _ClassVar[int]
    metadata: ResourceMetadata
    rules: _containers.RepeatedCompositeFieldContainer[EgressRule]
    def __init__(self, metadata: _Optional[_Union[ResourceMetadata, _Mapping]] = ..., rules: _Optional[_Iterable[_Union[EgressRule, _Mapping]]] = ...) -> None: ...

class EgressRule(_message.Message):
    __slots__ = ("http", "tls_passthrough")
    HTTP_FIELD_NUMBER: _ClassVar[int]
    TLS_PASSTHROUGH_FIELD_NUMBER: _ClassVar[int]
    http: HttpRule
    tls_passthrough: TlsPassthroughRule
    def __init__(self, http: _Optional[_Union[HttpRule, _Mapping]] = ..., tls_passthrough: _Optional[_Union[TlsPassthroughRule, _Mapping]] = ...) -> None: ...

class HttpRule(_message.Message):
    __slots__ = ("hostnames", "ports")
    HOSTNAMES_FIELD_NUMBER: _ClassVar[int]
    PORTS_FIELD_NUMBER: _ClassVar[int]
    hostnames: _containers.RepeatedScalarFieldContainer[str]
    ports: Ports
    def __init__(self, hostnames: _Optional[_Iterable[str]] = ..., ports: _Optional[_Union[Ports, _Mapping]] = ...) -> None: ...

class TlsPassthroughRule(_message.Message):
    __slots__ = ("hostnames", "ports")
    HOSTNAMES_FIELD_NUMBER: _ClassVar[int]
    PORTS_FIELD_NUMBER: _ClassVar[int]
    hostnames: _containers.RepeatedScalarFieldContainer[str]
    ports: Ports
    def __init__(self, hostnames: _Optional[_Iterable[str]] = ..., ports: _Optional[_Union[Ports, _Mapping]] = ...) -> None: ...

class Ports(_message.Message):
    __slots__ = ("all", "numbers")
    ALL_FIELD_NUMBER: _ClassVar[int]
    NUMBERS_FIELD_NUMBER: _ClassVar[int]
    all: AllPorts
    numbers: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, all: _Optional[_Union[AllPorts, _Mapping]] = ..., numbers: _Optional[_Iterable[int]] = ...) -> None: ...

class AllPorts(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class CreateActorEgressPolicyRequest(_message.Message):
    __slots__ = ("actor", "egress_policy")
    ACTOR_FIELD_NUMBER: _ClassVar[int]
    EGRESS_POLICY_FIELD_NUMBER: _ClassVar[int]
    actor: ObjectRef
    egress_policy: EgressPolicy
    def __init__(self, actor: _Optional[_Union[ObjectRef, _Mapping]] = ..., egress_policy: _Optional[_Union[EgressPolicy, _Mapping]] = ...) -> None: ...
