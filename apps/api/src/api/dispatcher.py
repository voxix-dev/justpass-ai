"""Runs each round of a job on an agent, outside the request that started it.

A round starts after its row is committed and ends by saving the agent's answer. The steps are in
this order so a student's reply cannot reach an agent that is still being put away: the actor
hibernates (or is released) first, and only then does the job leave `processing`.

A run with no answer always releases its actor: if the actor crashed, it can't be resumed, and the
next try needs a fresh one.

Rounds are claimed in the database, so any number of API processes can share the work. A claim is a
lease on the round's row, renewed while the agent works. A process that dies stops renewing, and a
sweep (at start-up and every SWEEP_SECONDS) hands the round to a live process. A round claimed
MAX_CLAIMS times is left alone, which bounds what a round that keeps crashing costs. The lease
and the sweep use the database clock only, so processes need no synchronized clocks.
"""

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, func, literal_column, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api.agents import Agents
from api.context_token import mint_token
from api.db import DATABASE_DOWN
from api.tables import Artifact, Job, JobRound
from shared.agent_run import AgentRun, ContextGrant
from shared.models import ArtifactKind, JobStatus

ATTEMPTS = 2  # one retry, then the job fails
CLEAN_UP_ATTEMPTS = 3  # a resume in flight makes Substrate refuse a delete for a moment
CLEAN_UP_DELAY = 1.0
DATABASE_DELAY = 5.0
LEASE_SECONDS = 60  # a round nobody renewed for this long can be claimed again
RENEW_SECONDS = 20
SWEEP_SECONDS = 30
GIVE_BACK_SECONDS = 5  # how long a shutdown waits to release a round's lease
MAX_CLAIMS = 3  # how many times a round may be claimed, so one that keeps crashing stops
LEASE = literal_column(f"interval '{LEASE_SECONDS} seconds'")

log = logging.getLogger("uvicorn.error")


class Dispatcher:
    def __init__(self, agents: Agents, open_session: Callable[[], AsyncSession]) -> None:
        self.agents = agents
        self.open_session = open_session
        self._tasks: set[asyncio.Task[None]] = set()

    def start(self, job_id: uuid.UUID, number: int) -> None:
        """Run a committed round in the background, unless a process (this one too) already is."""
        self._spawn(self._run(job_id, number))

    def release(self, job_id: uuid.UUID) -> None:
        """Drop the agent of a job that is over, in the background."""
        self._spawn(self._quietly(self.agents.release, job_id))

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)  # the event loop keeps only a weak reference
        task.add_done_callback(self._tasks.discard)

    async def recover(self) -> None:
        """Start every unfinished round that nobody is running: never claimed, or its lease ran out.

        A round already claimed MAX_CLAIMS times is only logged. The claim itself happens in _run,
        so two processes that find the same round cannot both run it.
        """
        async with self.open_session() as session:
            sibling = aliased(JobRound)
            newest = (
                select(func.max(sibling.number))
                .where(sibling.job_id == JobRound.job_id)
                .scalar_subquery()
            )
            rows = await session.execute(
                select(JobRound.job_id, JobRound.number, JobRound.claims)
                .join(Job, Job.id == JobRound.job_id)
                .where(
                    Job.status.in_([JobStatus.RECEIVED, JobStatus.PROCESSING]),
                    JobRound.number == newest,
                    JobRound.failed_at.is_(None),
                    or_(
                        JobRound.lease_until.is_(None),
                        JobRound.lease_until < func.clock_timestamp(),
                    ),
                )
            )
            unclaimed = rows.all()
        for job_id, number, claims in unclaimed:
            if claims >= MAX_CLAIMS:
                log.error(
                    "Round %d of job %s was claimed %d times: not running it",
                    number,
                    job_id,
                    claims,
                )
            else:
                self.start(job_id, number)

    def start_sweeping(self) -> None:
        """Look for abandoned rounds every SWEEP_SECONDS until close()."""
        self._spawn(self._sweep_forever())

    async def _sweep_forever(self) -> None:
        """Run recover() every SWEEP_SECONDS, to take over the rounds of a process that died."""
        while True:
            await asyncio.sleep(SWEEP_SECONDS)
            try:
                await self.recover()
            except Exception:
                log.exception("Could not look for unfinished jobs")

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
        """Cancel background work, and wait until the rounds in flight have given their leases back.

        recover() can pick up unfinished received or processing jobs.
        """
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run(self, job_id: uuid.UUID, number: int) -> None:
        """Claim a round, run it and save its result if still current; unexpected errors stop it."""
        lease = uuid.uuid4()  # one per run, so a claim whose reply was lost is recognised as ours
        try:
            claim = await self._keep_trying(lambda: self._begin(job_id, number, lease))
            if claim is None:
                return
            run, user_id = claim
            lost = asyncio.Event()
            solving = asyncio.create_task(self._solve(run, user_id, lease))
            # The lease is renewed until the result is about to be saved: putting the actor away
            # can take longer than a lease.
            renewing = asyncio.create_task(self._renew(job_id, number, lease, solving, lost))
            try:
                try:
                    answer = await solving
                except asyncio.CancelledError:
                    if lost.is_set():  # the renewal found another run holding the round
                        return
                    raise
                if not await self._keep_trying(lambda: self._is_current(job_id, number, lease)):
                    return  # another run of this round took over, or finished first
                put_away = self.agents.release if answer is None else self.agents.hibernate
                await self._quietly(put_away, job_id)
            finally:
                solving.cancel()
                renewing.cancel()
                await asyncio.gather(solving, renewing, return_exceptions=True)
            await self._keep_trying(lambda: self._finish(job_id, number, lease, answer))
        except asyncio.CancelledError:
            # A graceful shutdown: hand the round back at once, not after the lease runs out.
            await self._give_back(job_id, number, lease)
            raise
        except Exception:
            log.exception("Round %d of job %s stopped", number, job_id)

    async def _give_back(self, job_id: uuid.UUID, number: int, lease: uuid.UUID) -> None:
        """Release this run's lease and undo its claim, if the lease is still ours.

        Only a clean shutdown comes here, so it must not count against MAX_CLAIMS: a crash, which
        leaves the lease to run out, still does. Waits at most GIVE_BACK_SECONDS.
        """
        try:
            async with asyncio.timeout(GIVE_BACK_SECONDS), self.open_session() as session:
                await session.execute(
                    update(JobRound)
                    .where(
                        JobRound.job_id == job_id,
                        JobRound.number == number,
                        JobRound.lease_id == lease,
                    )
                    .values(
                        lease_id=None,
                        lease_until=None,
                        claims=func.greatest(JobRound.claims - 1, 0),
                    )
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
        except Exception:
            log.exception("Could not give back round %d of job %s", number, job_id)

    async def _renew(
        self,
        job_id: uuid.UUID,
        number: int,
        lease: uuid.UUID,
        solving: asyncio.Task[str | None],
        lost: asyncio.Event,
    ) -> None:
        """Extend the lease every RENEW_SECONDS until cancelled, and stop the solve if it is lost.

        Any error is logged and the next try goes on: a database outage must not end the renewal.
        """
        while True:
            await asyncio.sleep(RENEW_SECONDS)
            try:
                async with self.open_session() as session:
                    renewed = await session.execute(
                        update(JobRound)
                        .where(
                            JobRound.job_id == job_id,
                            JobRound.number == number,
                            JobRound.lease_id == lease,
                        )
                        .values(lease_until=func.clock_timestamp() + LEASE)
                        .execution_options(synchronize_session=False)
                    )
                    await session.commit()
            except Exception:
                log.exception("Could not renew the lease on round %d of job %s", number, job_id)
                continue
            if renewed.rowcount == 0:  # pyright: ignore[reportAttributeAccessIssue]
                log.warning("Lost the lease on round %d of job %s", number, job_id)
                lost.set()
                solving.cancel()  # another run owns the round: do not pay for this one
                return

    async def _is_current(self, job_id: uuid.UUID, number: int, lease: uuid.UUID) -> bool:
        async with self.open_session() as session:
            return await self._current(session, job_id, number, lease) is not None

    async def _current(
        self, session: AsyncSession, job_id: uuid.UUID, number: int, lease: uuid.UUID
    ) -> Job | None:
        """Lock the job, and return it if this run still holds the round it is processing."""
        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None or job.status != JobStatus.PROCESSING:
            return None
        newest = await session.scalar(
            select(func.max(JobRound.number)).where(JobRound.job_id == job_id)
        )
        holder = await session.scalar(
            select(JobRound.lease_id)
            .where(JobRound.job_id == job_id, JobRound.number == number)
            .with_for_update()
        )
        return job if newest == number and holder == lease else None

    async def _begin(
        self, job_id: uuid.UUID, number: int, lease: uuid.UUID
    ) -> tuple[AgentRun, str] | None:
        """Claim the round: return the agent's task and its user, and set a received job processing.

        Return None if the job is missing or has another status, the round is not its newest, or
        another run holds it (or it was claimed MAX_CLAIMS times). Claiming again with the same
        lease changes nothing, so a commit whose reply was lost can be repeated.
        """
        async with self.open_session() as session:
            job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None or job.status not in (JobStatus.RECEIVED, JobStatus.PROCESSING):
                return None
            newest = await session.scalar(
                select(func.max(JobRound.number)).where(JobRound.job_id == job_id)
            )
            if newest != number:
                return None
            now = func.clock_timestamp()
            claimed = await session.execute(
                update(JobRound)
                .where(
                    JobRound.job_id == job_id,
                    JobRound.number == number,
                    JobRound.failed_at.is_(None),
                    or_(
                        JobRound.lease_id == lease,
                        and_(
                            JobRound.claims < MAX_CLAIMS,
                            or_(JobRound.lease_until.is_(None), JobRound.lease_until < now),
                        ),
                    ),
                )
                .values(
                    lease_id=lease,
                    lease_until=now + LEASE,
                    claims=JobRound.claims + case((JobRound.lease_id == lease, 0), else_=1),
                )
                .returning(JobRound.feedback)
                .execution_options(synchronize_session=False)
            )
            row = claimed.one_or_none()
            if row is None:
                return None
            if job.status == JobStatus.RECEIVED:
                job.move_to(JobStatus.PROCESSING, datetime.now(UTC))
            run = AgentRun(
                job_id=job.id,
                round=number,
                text=job.input_text or "",
                feedback=row.feedback,
            )
            await session.commit()
            return run, job.user_id

    @staticmethod
    def _grant(
        job_id: uuid.UUID, user_id: str, number: int, lease: uuid.UUID
    ) -> ContextGrant | None:
        """The agent's way into the Context API for this round, or None when there is none."""
        url = os.environ.get("CONTEXT_API_URL", "")
        return (
            ContextGrant(url=url, token=mint_token(job_id, user_id, number, lease)) if url else None
        )

    async def _solve(self, run: AgentRun, user_id: str, lease: uuid.UUID) -> str | None:
        """Return the answer, or None if the agent gives none or both attempts raise an error.

        Each attempt gets a token of its own, so a retry long after the first never holds one that
        is about to expire.
        """
        for attempt in range(1, ATTEMPTS + 1):
            try:
                grant = self._grant(run.job_id, user_id, run.round, lease)
                return (await self.agents.solve(run.model_copy(update={"context": grant}))).answer
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

    async def _finish(
        self, job_id: uuid.UUID, number: int, lease: uuid.UUID, answer: str | None
    ) -> None:
        """Save the result and give up the lease, unless another run of this round took over.

        An absent answer fails the first round: failed is final. A later round is marked failed
        but kept, with what the agent wrote in it, and the job returns to review. The student can
        accept the earlier answer or ask again.
        """
        async with self.open_session() as session:
            job = await self._current(session, job_id, number, lease)
            if job is None:
                return
            now = datetime.now(UTC)
            row = await session.get_one(JobRound, (job_id, number), populate_existing=True)
            row.lease_id = None
            row.lease_until = None
            if answer is not None:
                session.add(
                    Artifact(job_id=job_id, round=number, kind=ArtifactKind.FINAL, content=answer)
                )
                job.move_to(JobStatus.AWAITING_REVIEW, now)
            else:
                row.failed_at = now
                job.move_to(JobStatus.AWAITING_REVIEW if number > 1 else JobStatus.FAILED, now)
            await session.commit()
