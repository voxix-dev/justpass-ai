from typing import Any, cast

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.context import ContextClient
from agent.notes import MAX_DOC_CHARS, apply_notes, fit
from agent.prompt import task_message
from agent.roles.curator import Curation, curate, curator_message
from shared.context import Memory, Profile
from shared.models import Level


def test_a_note_is_added_as_a_bullet_to_a_doc_that_is_not_a_bullet_list():
    body, unmatched = apply_notes("Likes short steps", ["Prefers bold, not boxed"], [])
    assert body == "Likes short steps\n- Prefers bold, not boxed"
    assert unmatched == []


def test_a_note_already_there_is_not_added_again_whatever_its_case():
    body, _ = apply_notes("- Likes short steps", ["likes  short steps", "Uses a pen"], [])
    assert body == "- Likes short steps\n- Uses a pen"


def test_a_note_added_as_a_bullet_matches_the_same_note_without_one():
    body, _ = apply_notes("- Likes short steps", ["- likes short steps", "- Uses a pen"], [])
    assert body == "- Likes short steps\n- Uses a pen"


def test_a_removal_drops_exactly_the_matching_line_and_reports_the_rest():
    body = "- Likes short steps\n- Uses a pen\n- Likes short steps in physics"
    new, unmatched = apply_notes(body, [], ["Likes short steps", "- Uses a pen", "Likes boxes"])
    assert new == "- Likes short steps in physics"
    assert unmatched == ["Likes boxes"]


def test_a_nul_character_is_not_saved():
    body, _ = apply_notes("", ["Uses a\x00 pen"], [])
    assert body == "- Uses a pen"


def test_the_oldest_task_lines_go_first_then_the_oldest_lines():
    lines = ["- Oldest", "- Task: a", "- Task: b", "- Newest"]
    assert fit("\n".join(lines)) == "\n".join(lines)
    long = ["- Oldest", *(f"- Task: {n} " + "x" * 90 for n in range(100)), "- Newest"]
    fitted = fit("\n".join(long))
    assert len(fitted) <= MAX_DOC_CHARS
    assert fitted.splitlines()[0] == "- Oldest"
    assert fitted.splitlines()[-1] == "- Newest"
    assert "- Task: 0 " not in fitted
    assert "- Task: 99 " in fitted
    plain = [f"- Note {n:03d} " + "y" * 90 for n in range(200)]
    fitted = fit("\n".join(plain))
    assert len(fitted) <= MAX_DOC_CHARS
    assert fitted.splitlines()[-1] == plain[-1]
    assert plain[0] not in fitted


def test_the_curation_limits_each_list_to_twenty_notes():
    schema = Curation.model_json_schema()["$defs"]["NotesChange"]["properties"]
    assert schema["add"]["maxItems"] == schema["remove"]["maxItems"] == 20
    assert schema["add"]["items"]["maxLength"] == 500


def test_the_curator_message_fences_the_texts_and_cuts_a_long_answer():
    message = curator_message(
        subject="math",
        round_number=1,
        task_text="Solve",
        feedback=None,
        answer="a" * 5_000,
        profile_notes="",
        subject_notes="",
    )
    assert "feedback" not in message
    assert "a" * 4_000 in message
    assert "a" * 4_001 not in message
    assert "Profile notes now:\n\n(none)" in message
    assert "Subject notes now:\n\n(none)" in message


class FakeContext:
    def __init__(self) -> None:
        self.saves: list[tuple[str, str]] = []

    async def update_memory(self, scope: str, subject: str | None, body: str, version: int) -> None:
        self.saves.append((scope, body))


def curation_model(payload: dict[str, Any]) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])

    return FunctionModel(respond)


async def run_curate(payload: dict[str, Any], feedback: str | None) -> FakeContext:
    context = FakeContext()
    await curate(
        curation_model(payload),
        cast(ContextClient, context),
        task_text="Solve",
        subject="general",
        round_number=1,
        feedback=feedback,
        answer=None,
        profile=Profile(
            level=Level.COLLEGE,
            language="pl",
            subject="general",
            profile_md="- Likes bold",
            profile_version=1,
        ),
        subject_memory=Memory(subject="general", content_md="- Use Sarrus", version=1),
    )
    return context


REMOVES = {
    "profile": {"add": [], "remove": ["Likes bold"]},
    "subject": {"add": [], "remove": ["Use Sarrus"]},
}


@pytest.mark.anyio
async def test_without_feedback_the_removals_are_dropped_and_nothing_is_saved():
    assert (await run_curate(REMOVES, None)).saves == []


@pytest.mark.anyio
async def test_without_feedback_the_additions_are_still_saved():
    payload = {**REMOVES, "subject": {"add": ["Uses Laplace"], "remove": ["Use Sarrus"]}}
    saves = (await run_curate(payload, None)).saves
    assert saves == [("subject", "- Use Sarrus\n- Uses Laplace")]


@pytest.mark.anyio
async def test_with_feedback_the_removals_apply():
    saves = (await run_curate(REMOVES, "That rule is wrong")).saves
    assert sorted(saves) == [("profile", ""), ("subject", "")]


def test_the_first_message_says_to_answer_in_the_language_of_the_task():
    message = task_message(
        "Oblicz",
        Profile(level=Level.COLLEGE, language="en", subject="x", profile_md="", profile_version=0),
        [],
    )
    assert "in the language the task is written in" in message
    assert "use the language with code 'en'" in message


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
