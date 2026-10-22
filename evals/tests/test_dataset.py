from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.dataset import load_samples
from evals.models import Sample

MATH = """\
id: {id}
subject: math
level: high_school
language: en
prompt: Solve 3x + 7 = 22.
answers: ["5"]
source: synthetic
"""


def test_math_sample_needs_an_answer():
    with pytest.raises(ValidationError, match="accepted answer"):
        Sample.model_validate(
            {"id": "a", "subject": "math", "level": "college", "language": "en",
             "prompt": "?", "source": "synthetic"}
        )  # fmt: skip


def test_essay_sample_needs_a_rubric():
    with pytest.raises(ValidationError, match="rubric"):
        Sample.model_validate(
            {"id": "a", "subject": "essay", "level": "college", "language": "en",
             "prompt": "?", "source": "real"}
        )  # fmt: skip


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Sample.model_validate(
            {"id": "a", "subject": "math", "level": "college", "language": "en",
             "prompt": "?", "answers": ["1"], "source": "real", "student_name": "Ala"}
        )  # fmt: skip


def test_numeric_answers_become_strings():
    sample = Sample.model_validate(
        {"id": "a", "subject": "math", "level": "college", "language": "en",
         "prompt": "?", "answers": [5, 0.5], "source": "real"}
    )  # fmt: skip
    assert sample.answers == ["5", "0.5"]


def test_load_errors_name_the_file(tmp_path: Path):
    (tmp_path / "linear.yaml").write_text("id: linear\n")
    with pytest.raises(ValueError, match=r"linear\.yaml"):
        load_samples(tmp_path)


def test_yml_files_are_rejected(tmp_path: Path):
    (tmp_path / "linear.yml").write_text(MATH.format(id="linear"))
    with pytest.raises(ValueError, match=r"rename to \.yaml"):
        load_samples(tmp_path)


def test_load_samples(tmp_path: Path):
    (tmp_path / "math").mkdir()
    (tmp_path / "math" / "linear.yaml").write_text(MATH.format(id="linear"))
    [sample] = load_samples(tmp_path)
    assert sample.answers == ["5"]


def test_id_must_match_file_name(tmp_path: Path):
    (tmp_path / "other.yaml").write_text(MATH.format(id="linear"))
    with pytest.raises(ValueError, match="file name"):
        load_samples(tmp_path)


def test_duplicate_ids_across_folders(tmp_path: Path):
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "linear.yaml").write_text(MATH.format(id="linear"))
    with pytest.raises(ValueError, match="duplicate"):
        load_samples(tmp_path)


def test_image_path_is_resolved_and_must_exist(tmp_path: Path):
    (tmp_path / "linear.yaml").write_text(MATH.format(id="linear") + "image: page.png\n")
    with pytest.raises(ValueError, match="not found"):
        load_samples(tmp_path)
    (tmp_path / "page.png").write_bytes(b"png")
    [sample] = load_samples(tmp_path)
    assert sample.image == tmp_path / "page.png"


def test_image_must_stay_inside_the_samples_dir(tmp_path: Path):
    samples = tmp_path / "samples"
    samples.mkdir()
    (tmp_path / "secret.png").write_bytes(b"png")
    (samples / "linear.yaml").write_text(MATH.format(id="linear") + "image: ../secret.png\n")
    with pytest.raises(ValueError, match="inside"):
        load_samples(samples)
