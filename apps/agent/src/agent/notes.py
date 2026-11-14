"""The notes on the student: two Markdown docs of bullet lines, the profile and one per subject.

Changes are applied to the doc a writer holds and saved with compare-and-set: a doc that changed
meanwhile is read again and the changes applied to it.
"""

from typing import Annotated, Literal

import httpx2
from pydantic import StringConstraints

from agent.context import ContextClient

MAX_SAVE_ATTEMPTS = 3  # a save that finds the doc changed meanwhile applies again
MAX_DOC_CHARS = 10_000  # the limit of the Context API
TASK_PREFIX = "- Task:"

Scope = Literal["profile", "subject"]
Note = Annotated[str, StringConstraints(max_length=500)]


def note_text(line: str) -> str:
    return line[2:].strip() if line.startswith("- ") else line.strip()


def apply_notes(body: str, add: list[str], remove: list[str]) -> tuple[str, list[str]]:
    """Apply the changes to a doc of bullet lines: the new doc, and the removals that match none."""
    dropped = {note_text(item).casefold() for item in remove}
    lines = [line.rstrip() for line in body.splitlines() if line.strip()]
    present = {note_text(line).casefold() for line in lines}
    unmatched = [item for item in remove if note_text(item).casefold() not in present]
    kept = [line for line in lines if note_text(line).casefold() not in dropped]
    seen = {note_text(line).casefold() for line in kept}
    for item in add:
        text = " ".join(item.replace("\x00", "").split())
        if text and text.casefold() not in seen:
            kept.append(f"- {text}")
            seen.add(text.casefold())
    return "\n".join(kept), unmatched


def fit(body: str) -> str:
    """Drop the oldest Task lines, then the oldest lines, until the doc fits the API's limit."""
    lines = body.splitlines()
    while lines and len("\n".join(lines)) > MAX_DOC_CHARS:
        oldest = next((i for i, line in enumerate(lines) if line.startswith(TASK_PREFIX)), 0)
        del lines[oldest]
    return "\n".join(lines)


def _normalized(body: str) -> str:
    return "\n".join(line.rstrip() for line in body.splitlines() if line.strip())


async def _reread(context: ContextClient, scope: Scope) -> tuple[str, int]:
    if scope == "profile":
        profile = await context.get_profile()
        return profile.profile_md, profile.profile_version
    memory = await context.get_subject_memory()
    return memory.content_md, memory.version


async def save_notes(
    context: ContextClient,
    scope: Scope,
    add: list[str],
    remove: list[str],
    body: str,
    version: int,
) -> str:
    """Apply the changes to the doc and save it; a doc that changed meanwhile is read again.

    A removal that matches nothing is ignored. Returns what happened: saved, unchanged or conflict.
    """
    for _ in range(MAX_SAVE_ATTEMPTS):
        new_body, _unmatched = apply_notes(body, add, remove)
        new_body = fit(new_body)
        if new_body == _normalized(body):
            return "unchanged"
        try:
            await context.update_memory(scope, None, new_body, version)
        except httpx2.HTTPStatusError as error:
            if error.response.status_code != 412:
                raise
            body, version = await _reread(context, scope)
            continue
        return "saved"
    return "conflict"
