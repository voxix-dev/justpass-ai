"""run_job: one job end to end. The orchestrator plans and delegates with run_agent; workers do
the work and pass artifacts to each other by id, never by content."""

import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial

from pydantic import BaseModel, Field, JsonValue, ValidationError

from agent.config import Limits, model_for
from agent.llm import ChatModel, LLMError, OpenRouterClient
from agent.loop import AgentError, Budget, BudgetExceeded, ToolHandler, run_agent_loop
from agent.playbook import Playbook, load_playbooks
from agent.prompts import ORCHESTRATOR_PROMPT, ROLE_JOBS, WORKER_PROMPT
from agent.roles import tools_for
from agent.tools import (
    CONTEXT_TOOLS,
    HttpContextClient,
    RunAgent,
    SandboxClient,
    ToolBackend,
    ToolError,
    UnavailableTool,
    WrittenArtifact,
)
from shared.agent_run import JobRequest, JobResult
from shared.models import AgentResult, ResearchBrief, Role, Tier

log = logging.getLogger(__name__)


class WorkerAnswer(BaseModel):
    artifact_id: str
    summary: str = Field(max_length=600)
    confidence: float = Field(ge=0, le=1)


class FinalAnswer(BaseModel):
    final_artifact_id: str
    summary: str = Field(max_length=2000)


@dataclass(frozen=True)
class Deps:
    llm: ChatModel
    context: Callable[[Role], ToolBackend]  # one client per role: tokens are scoped to a role
    web: ToolBackend = field(default_factory=UnavailableTool)
    code: ToolBackend = field(default_factory=UnavailableTool)
    limits: Limits = field(default_factory=Limits)

    @classmethod
    def from_env(cls, context_tokens: Mapping[Role, str]) -> Deps:
        """Real clients. The API mints one Context API token per role for each job."""
        limits = Limits()
        llm = OpenRouterClient.from_env(
            timeout_s=limits.call_timeout_s, max_retries=limits.max_retries
        )
        needed = {role for role in Role if tools_for(role) & CONTEXT_TOOLS}
        if missing := needed - context_tokens.keys():
            raise ValueError(f"no Context API token for {sorted(missing)}")
        url = os.environ["CONTEXT_API_URL"]
        clients = {role: HttpContextClient(url, token) for role, token in context_tokens.items()}
        # The API relays run_code to the job's sandbox actor (the solver's token allows it).
        auth = {"Authorization": f"Bearer {context_tokens[Role.SOLVER]}"}
        code = SandboxClient(f"{url.rstrip('/')}/v1/context/run_code", auth)
        return cls(llm=llm, context=clients.__getitem__, code=code, limits=limits)


async def run_job(request: JobRequest, deps: Deps) -> JobResult:
    book = load_playbooks().get(request.playbook)
    if book is None:
        raise ValueError(f"unknown playbook {request.playbook!r}")
    return await _Job(request, book, deps).run()


def _parse[T: BaseModel](model: type[T], text: str) -> T | None:
    """Models like to wrap JSON in prose or code fences: use the first {...} that fits."""
    decoder = json.JSONDecoder()
    for start in (i for i, char in enumerate(text) if char == "{"):
        try:
            return model.model_validate(decoder.raw_decode(text, start)[0])
        except ValueError:  # includes ValidationError
            continue
    return None


def _failed(summary: str) -> JsonValue:
    result = AgentResult(artifact_id="", summary=summary[:600], status="error", confidence=0)
    return result.model_dump(mode="json")


class _Job:
    def __init__(self, request: JobRequest, book: Playbook, deps: Deps) -> None:
        self.request, self.book, self.deps = request, book, deps
        self.budget = Budget(deps.limits)
        self.known_artifacts = set(request.input_artifact_ids)
        self.passed_review: set[str] = set()
        self.tiers: dict[Role, set[Tier]] = {}
        for step in book.steps:
            self.tiers.setdefault(step.role, set()).update(
                {step.tier, step.escalate_to or step.tier}
            )

    async def run(self) -> JobResult:
        steps = "\n".join(
            f"- {s.role}: {s.tier}, {s.escalate_to or 'no escalation'}"
            + (" (one run per task, all in one turn)" if s.parallel_per_task else "")
            for s in self.book.steps
        )
        system = ORCHESTRATOR_PROMPT.format(
            name=self.book.name, description=self.book.description, steps=steps
        )
        try:
            text = await self._loop(
                Role.ORCHESTRATOR,
                Tier.STRONG,
                system,
                self.request.task,
                self.request.input_artifact_ids,
                set(),
            )
        except (BudgetExceeded, AgentError, LLMError) as e:
            return self._result(error=str(e))
        except Exception:
            log.exception("job %s crashed", self.request.job_id)
            return self._result(error="internal error")
        final = _parse(FinalAnswer, text)
        if final is None:
            return self._result(error="the orchestrator gave no valid final answer")
        if final.final_artifact_id not in self.passed_review:
            return self._result(error="the final artifact did not pass review", final=final)
        return self._result(final=final)

    def _result(self, error: str | None = None, final: FinalAnswer | None = None) -> JobResult:
        return JobResult(
            job_id=self.request.job_id,
            status="failed" if error else "ok",
            final_artifact_id=final.final_artifact_id if final else None,
            summary=final.summary if final else "",
            error=error,
            turns=self.budget.turns,
            cost_usd=self.budget.cost_usd,
        )

    async def _loop(
        self, role: Role, tier: Tier, system: str, task: str, inputs: list[str], written: set[str]
    ) -> str:
        user = f"Task:\n{task}\n\nInput artifact ids: {', '.join(inputs) or 'none'}"
        return await run_agent_loop(
            role=role,
            model=model_for(role, tier),
            system=system,
            user=user,
            llm=self.deps.llm,
            budget=self.budget,
            session_id=self.request.job_id,
            handlers=self._handlers(role, written),
        )

    def _handlers(self, role: Role, written: set[str]) -> dict[str, ToolHandler]:
        handlers: dict[str, ToolHandler] = {}
        for name in tools_for(role):
            if name in CONTEXT_TOOLS:
                backend = self.deps.context(role)
            else:
                backend = self.deps.code if name == "run_code" else self.deps.web
            handlers[name] = partial(backend.call, name)
        if "write_artifact" in handlers:
            handlers["write_artifact"] = partial(self._write_artifact, role, written)
        if "run_agent" in handlers:
            handlers["run_agent"] = self._run_agent
        return handlers

    async def _write_artifact(self, role: Role, written: set[str], args: BaseModel) -> JsonValue:
        out = await self.deps.context(role).call("write_artifact", args)
        try:
            written.add(WrittenArtifact.model_validate(out).artifact_id)
        except ValidationError as e:
            raise ToolError("the context API returned no artifact id") from e
        return out

    async def _run_agent(self, args: BaseModel) -> JsonValue:
        if not isinstance(args, RunAgent):
            raise TypeError(f"expected RunAgent, got {type(args).__name__}")
        role, tier = Role(args.role), Tier(args.tier)
        if role not in self.tiers:
            raise ToolError(f"the {self.book.name} playbook has no {role} step")
        if tier not in self.tiers[role]:
            raise ToolError(f"{role} may only run on tiers {sorted(self.tiers[role])}")
        if unknown := set(args.input_artifact_ids) - self.known_artifacts:
            raise ToolError(f"unknown artifact ids {sorted(unknown)}")
        if role == Role.RESEARCH:
            try:
                ResearchBrief.model_validate_json(args.task)
            except ValidationError as e:
                raise ToolError(f"a research task must be ResearchBrief JSON: {e}") from e
        written: set[str] = set()
        system = WORKER_PROMPT.format(role=role, job=ROLE_JOBS[role])
        try:
            text = await self._loop(role, tier, system, args.task, args.input_artifact_ids, written)
        except (AgentError, LLMError) as e:
            return _failed(f"{role} agent failed: {e}")
        finally:
            self.known_artifacts |= written
        answer = _parse(WorkerAnswer, text)
        if answer is None:
            return _failed(f"{role} agent gave no valid answer")
        if answer.artifact_id not in written | set(args.input_artifact_ids):
            return _failed(f"{role} agent named an artifact it did not write or get")
        if role == Role.REVIEW:
            if answer.summary.startswith("PASS"):
                self.passed_review.add(answer.artifact_id)
            else:
                self.passed_review.discard(answer.artifact_id)
        if role == Role.RESEARCH:
            # Research read the open web: its words must not steer the orchestrator.
            answer.summary = "Notes saved."
        return AgentResult.model_validate(answer.model_dump() | {"status": "ok"}).model_dump()
