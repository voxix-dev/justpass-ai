from pathlib import Path

import yaml
from pydantic import ValidationError

from evals.models import Sample


def load_samples(samples_dir: Path) -> list[Sample]:
    """Load every *.yaml under samples_dir. The id must equal the file name."""
    if stray := sorted(samples_dir.rglob("*.yml")):
        raise ValueError(f"rename to .yaml: {stray[0]}")
    samples: list[Sample] = []
    for path in sorted(samples_dir.rglob("*.yaml")):
        try:
            sample = Sample.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        except (ValidationError, yaml.YAMLError) as e:
            raise ValueError(f"{path}: {e}") from e
        if sample.id != path.stem:
            raise ValueError(f"{path}: id {sample.id!r} does not match the file name")
        if sample.image is not None:
            image = (path.parent / sample.image).resolve()
            if not image.is_relative_to(samples_dir.resolve()):
                raise ValueError(f"{path}: image must be inside {samples_dir}")
            if not image.is_file():
                raise ValueError(f"{path}: image {sample.image} not found")
            sample = sample.model_copy(update={"image": image})
        samples.append(sample)
    ids = [s.id for s in samples]
    if dupes := {i for i in ids if ids.count(i) > 1}:
        raise ValueError(f"duplicate sample ids: {sorted(dupes)}")
    return samples
