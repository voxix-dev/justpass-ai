"""The one place that maps (role, tier) to a model, plus the hard limits of a job.

Model ids come from https://openrouter.ai/api/v1/models and each has a zero-data-retention
endpoint (https://openrouter.ai/api/v1/endpoints/zdr). Never use Anthropic Sonnet or Haiku.
"""

from pydantic import BaseModel

from shared.models import Role, Tier

OPUS = "anthropic/claude-opus-5.5"
SOL = "openai/gpt-6.1-sol"
OPEN_CHEAP = "deepseek/deepseek-v4.1-flash"

TIER_MODELS: dict[Tier, str] = {Tier.CHEAP: OPEN_CHEAP, Tier.MID: SOL, Tier.STRONG: OPUS}

# The orchestrator only plans, but the plan has to be right: always Opus.
MODELS: dict[tuple[Role, Tier], str] = {
    (role, tier): OPUS if role == Role.ORCHESTRATOR else TIER_MODELS[tier]
    for role in Role
    for tier in Tier
}


def model_for(role: Role, tier: Tier) -> str:
    return MODELS[(role, tier)]


class Limits(BaseModel):
    """Hard limits per job. Hitting one aborts the job cleanly."""

    max_turns: int = 60  # model calls across all agents of the job
    max_turns_per_agent: int = 15
    max_cost_usd: float = 1.0
    max_tokens_per_call: int = 8000
    call_timeout_s: float = 120
    max_retries: int = 3
