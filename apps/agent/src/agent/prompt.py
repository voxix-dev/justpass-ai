"""What the agent tells its models, shared by its roles, and how the fake shows feedback."""

import re

from shared.context import Profile, Task

NONE = "(none)"


def fence(text: str) -> str:
    """Put text in a fenced code block longer than any run of backticks inside it.

    The student's words are shown as they are: a fence they cannot close keeps them from
    becoming Markdown of their own.
    """
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{text}\n{ticks}"


def round_feedback(task: Task, round_number: int) -> list[str]:
    """What the student asked to change, in this round and the answered ones before it.

    A round that failed is left out: the student sees its text again and may send it anew.
    """
    return [
        r.feedback
        for r in task.rounds
        if r.feedback and (r.outcome == "answered" or r.number == round_number)
    ]


def task_message(
    text: str, profile: Profile | None, feedback: list[str], subject_notes: str = ""
) -> str:
    """The first message of a job, or the whole job again when the process has lost its memory."""
    parts = [f"Task:\n\n{fence(text)}"]
    if profile is not None:
        parts.append(
            f"Student: {profile.level} level, {profile.subject}. "
            "Write the answer in the language the task is written in, unless the notes on the "
            "student ask for another; if the task's language is unclear, use the language with "
            f"code {profile.language!r}."
        )
        if profile.profile_md:
            parts.append(f"Notes on the student:\n\n{fence(profile.profile_md)}")
        if subject_notes:
            parts.append(f"Notes on the student in {profile.subject}:\n\n{fence(subject_notes)}")
    if feedback:
        parts.append(
            "The student has asked for these changes, oldest first. "
            "Give one complete answer that includes them.\n\n"
            + "\n\n".join(fence(item) for item in feedback)
        )
    return "\n\n".join(parts)


def feedback_message(feedback: str, profile: Profile | None = None, subject_notes: str = "") -> str:
    """A later round, sent to the model that already has the job in its message history.

    The notes may have changed since the first message, even been cleared, so the current state of
    both comes with it.
    """
    parts = [
        f"The student asks for changes. Give the complete revised answer.\n\n{fence(feedback)}"
    ]
    if profile is not None:
        parts.append("These notes on the student replace any earlier notes in this conversation.")
        profile_notes = fence(profile.profile_md) if profile.profile_md else NONE
        parts.append(f"Notes on the student now:\n\n{profile_notes}")
        parts.append(
            f"Notes on the student in {profile.subject} now:\n\n"
            f"{fence(subject_notes) if subject_notes else NONE}"
        )
    return "\n\n".join(parts)
