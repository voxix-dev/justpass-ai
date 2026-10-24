"""Hands a job round to the agents. The dispatcher PR fills this in."""

import uuid
from collections.abc import Awaitable, Callable

type Dispatch = Callable[[uuid.UUID, int], Awaitable[None]]


async def dispatch_round(job_id: uuid.UUID, round_number: int) -> None:
    """Start the agent run for this round of the job. Called after the round is committed, so it
    must return quickly and never raise: the student's request has already succeeded.

    TODO(dispatcher): start or resume the job's actor, and move a new job to processing. Give
    the run one api.agent_token.mint_token(job_id, round_number, role) per role.
    """


def get_dispatch() -> Dispatch:
    """A dependency, so tests can check what was dispatched."""
    return dispatch_round
