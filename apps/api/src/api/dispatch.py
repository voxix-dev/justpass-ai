"""Hands a job round to the agents. The dispatcher PR fills this in."""

import uuid
from collections.abc import Awaitable, Callable

type Dispatch = Callable[[uuid.UUID, int], Awaitable[None]]


async def dispatch_round(job_id: uuid.UUID, round_number: int) -> None:
    """Dispatch hook for an agent round; currently a no-op.

    Called after the round is committed, so it must return quickly and never raise: the
    student's job has already been saved.

    TODO(dispatcher): start or resume the job's actor, and move a new job to processing.
    """


def get_dispatch() -> Dispatch:
    """A dependency, so tests can check what was dispatched."""
    return dispatch_round
