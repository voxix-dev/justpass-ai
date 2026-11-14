from agent.notes import MAX_DOC_CHARS, apply_notes, fit
from agent.roles.curator import Curation, curator_message


def test_a_note_is_added_as_a_bullet_to_a_doc_that_is_not_a_bullet_list():
    body, unmatched = apply_notes("Likes short steps", ["Prefers bold, not boxed"], [])
    assert body == "Likes short steps\n- Prefers bold, not boxed"
    assert unmatched == []


def test_a_note_already_there_is_not_added_again_whatever_its_case():
    body, _ = apply_notes("- Likes short steps", ["likes  short steps", "Uses a pen"], [])
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
