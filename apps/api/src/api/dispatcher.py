"""Runs each round of a job on an agent, outside the request that started it.

A round starts after its row is committed and ends by saving the agent's answer. The steps are in
this order so a student's reply cannot reach an agent that is still being put away: the actor
hibernates (or is released) first, and only then does the job leave `processing`.

A run with no answer always releases its actor: if the actor crashed, it can't be resumed, and the
next try needs a fresh one.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.agents import Agents
from api.db import DATABASE_DOWN
from api.tables import Artifact, Job, JobRound
from shared.agent_run import AgentRun
from shared.models import ArtifactKind, JobStatus

ATTEMPTS = 2  # one retry, then the job fails
CLEAN_UP_ATTEMPTS = 3  # a resume in flight makes Substrate refuse a delete for a moment
CLEAN_UP_DELAY = 1.0
DATABASE_DELAY = 5.0

log = logging.getLogger("uvicorn.error")


class Dispatcher:
    def __init__(self, agents: Agents, open_session: Callable[[], AsyncSession]) -> None:
        self.agents = agents
        self.open_session = open_session
        self._tasks: set[asyncio.Task[None]] = set()
        self._rounds: set[tuple[uuid.UUID, int]] = set()  # the ones this process is running

    def start(self, job_id: uuid.UUID, number: int) -> None:
        """Run a committed round in the background, unless this process already is."""
        if (job_id, number) not in self._rounds:
            self._rounds.add((job_id, number))
            self._spawn(self._run(job_id, number))

    def release(self, job_id: uuid.UUID) -> None:
        """Drop the agent of a job that is over, in the background."""
        self._spawn(self._quietly(self.agents.release, job_id))

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)  # the event loop keeps only a weak reference
        task.add_done_callback(self._tasks.discard)

    async def recover(self) -> None:
        """Start every unfinished round that this process is not running.

        shortcut: this assumes one API process. A second one would repeat the rounds of the first.
        The run that finishes second changes nothing, but it wastes an agent run and can leave the
        actor awake. Claim rounds in the database before running two replicas.
        """
        async with self.open_session() as session:
            rounds = await session.execute(
                select(Job.id, func.max(JobRound.number))
                .join(JobRound, JobRound.job_id == Job.id)
                .where(Job.status.in_([JobStatus.RECEIVED, JobStatus.PROCESSING]))
                .group_by(Job.id)
            )
            unfinished = rounds.all()
        for job_id, number in unfinished:
            self.start(job_id, number)

    async def recover_at_start_up(self) -> None:
        """Run recover() before any request. If the database is down, keep trying in the background.

        Requests are served meanwhile: /ready reports the database.
        """
        try:
            await self.recover()
        except DATABASE_DOWN:
            log.exception("Could not look for unfinished jobs")
            self._spawn(self._keep_trying(self.recover))

    async def _keep_trying[T](self, step: Callable[[], Awaitable[T]]) -> T:
        """Run a database step until Postgres answers: a round must not end on a short outage.

        Every step can run again after a commit whose reply was lost: a round already begun is
        reused, and one already finished is left alone. Errors outside DATABASE_DOWN propagate.
        """
        while True:
            try:
                return await step()
            except DATABASE_DOWN:
                log.exception("The database cannot be reached")
                await asyncio.sleep(DATABASE_DELAY)

    async def close(self) -> None:
        """Cancel background work; recover() can pick up unfinished received or processing jobs."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run(self, job_id: uuid.UUID, number: int) -> None:
        """Run a round and save its result if still current; unexpected errors stop the run."""
        try:
            run = await self._keep_trying(lambda: self._begin(job_id, number))
            if run is None:
                return
            answer = await self._solve(run)
            if not await self._keep_trying(lambda: self._is_current(job_id, number)):
                return  # another run of this round finished first
            put_away = self.agents.release if answer is None else self.agents.hibernate
            await self._quietly(put_away, job_id)
            await self._keep_trying(lambda: self._finish(job_id, number, answer))
        except Exception:
            log.exception("Round %d of job %s stopped", number, job_id)
        finally:
            self._rounds.discard((job_id, number))

    async def _is_current(self, job_id: uuid.UUID, number: int) -> bool:
        async with self.open_session() as session:
            return await self._current(session, job_id, number) is not None

    async def _current(self, session: AsyncSession, job_id: uuid.UUID, number: int) -> Job | None:
        """Lock the job, and return it if this round is the one it is processing."""
        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None or job.status != JobStatus.PROCESSING:
            return None
        newest = await session.scalar(
            select(func.max(JobRound.number)).where(JobRound.job_id == job_id)
        )
        return job if newest == number else None

    async def _begin(self, job_id: uuid.UUID, number: int) -> AgentRun | None:
        """Return the agent's task for a received or processing job, marking it processing.

        Return None if the job is missing or has another status.
        """
        async with self.open_session() as session:
            job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None or job.status not in (JobStatus.RECEIVED, JobStatus.PROCESSING):
                return None
            feedback = await session.scalar(
                select(JobRound.feedback).where(
                    JobRound.job_id == job_id, JobRound.number == number
                )
            )
            if job.status == JobStatus.RECEIVED:
                job.move_to(JobStatus.PROCESSING, datetime.now(UTC))
            run = AgentRun(
                job_id=job.id, round=number, text=job.input_text or "", feedback=feedback
            )
            await session.commit()
            return run

    async def _solve(self, run: AgentRun) -> str | None:
        """Return the answer, or None if the agent gives none or both attempts raise an error."""
        for attempt in range(1, ATTEMPTS + 1):
            try:
                return (await self.agents.solve(run)).answer
            except Exception:
                log.exception("Attempt %d of job %s failed", attempt, run.job_id)
        return None

    async def _quietly(
        self, step: Callable[[uuid.UUID], Awaitable[None]], job_id: uuid.UUID
    ) -> None:
        """Try clean-up up to three times, suppressing errors after the final attempt.

        A failed clean-up can leave an idle actor but does not discard the student's answer.
        """
        for attempt in range(1, CLEAN_UP_ATTEMPTS + 1):
            try:
                return await step(job_id)
            except Exception:
                log.exception("Clean-up %d of job %s failed", attempt, job_id)
            if attempt < CLEAN_UP_ATTEMPTS:
                await asyncio.sleep(CLEAN_UP_DELAY)

    async def _finish(self, job_id: uuid.UUID, number: int, answer: str | None) -> None:
        """Save the result, unless another run of this round already did.

        An absent answer fails the first round. Failed is final, so a failed revision drops its
        round and returns the job to review. The student can accept the answer or ask again.
        """
        async with self.open_session() as session:
            job = await self._current(session, job_id, number)
            if job is None:
                return
            now = datetime.now(UTC)
            if answer is not None:
                session.add(
                    Artifact(job_id=job_id, round=number, kind=ArtifactKind.FINAL, content=answer)
                )
                job.move_to(JobStatus.AWAITING_REVIEW, now)
            elif number > 1:
                await session.delete(await session.get_one(JobRound, (job_id, number)))
                job.move_to(JobStatus.AWAITING_REVIEW, now)
            else:
                job.move_to(JobStatus.FAILED, now)
            await session.commit()
