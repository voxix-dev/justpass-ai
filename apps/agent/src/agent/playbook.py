from importlib import resources

import yaml
from pydantic import BaseModel, ConfigDict

from shared.models import Role, Tier


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role
    tier: Tier
    escalate_to: Tier | None = None
    parallel_per_task: bool = False


class Playbook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    steps: list[Step]


def load_playbooks() -> dict[str, Playbook]:
    files = resources.files("agent").joinpath("playbooks").iterdir()
    books = [Playbook.model_validate(yaml.safe_load(f.read_text())) for f in files]
    return {b.name: b for b in books}
