"""The models of the roles: which model each one runs on and with what limits."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx2
from pydantic import SecretStr
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import (
    OpenRouterModel,
    OpenRouterModelSettings,
    OpenRouterProviderConfig,
)
from pydantic_ai.providers.openrouter import OpenRouterProvider

Role = Literal["orchestrator", "general", "math", "writer", "curator"]

FLASH = "deepseek/deepseek-v4.1-flash"
ROUTING: OpenRouterProviderConfig = {"sort": "price"}  # the cheapest provider of the model
MODEL_TIMEOUT_SECONDS = 60  # one request to the model


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    reasoning_tokens: int  # bounds the thinking before the reply
    max_output_tokens: int  # reasoning and reply together, so above the reasoning budget


ROLE_MODELS: dict[Role, ModelSpec] = {
    "orchestrator": ModelSpec(FLASH, 2_000, 4_000),
    "general": ModelSpec(FLASH, 4_000, 8_000),
    "math": ModelSpec(FLASH, 4_000, 8_000),
    "writer": ModelSpec(FLASH, 2_000, 8_000),
    # Less thinking than an answer needs: a long one ran into the output limit and lost the notes.
    "curator": ModelSpec(FLASH, 1_500, 8_000),
}


def make_models(key: SecretStr, http_client: httpx2.AsyncClient | None = None) -> dict[Role, Model]:
    """One model per role, all on one provider and so one HTTP client."""
    provider = OpenRouterProvider(api_key=key.get_secret_value(), http_client=http_client)
    return {
        role: OpenRouterModel(
            spec.model_id,
            provider=provider,
            settings=OpenRouterModelSettings(
                openrouter_provider=ROUTING,
                openrouter_reasoning={"max_tokens": spec.reasoning_tokens},
                max_tokens=spec.max_output_tokens,
                timeout=MODEL_TIMEOUT_SECONDS,
            ),
        )
        for role, spec in ROLE_MODELS.items()
    }


def _details(message: ModelMessage) -> dict[str, Any]:
    if isinstance(message, ModelResponse) and message.provider_details:
        return message.provider_details
    return {}


def _cost(message: ModelMessage) -> float:
    try:
        return float(_details(message).get("cost") or 0)
    except TypeError, ValueError:
        return 0.0


def reported_cost(messages: Sequence[ModelMessage]) -> float:
    """The USD cost OpenRouter reported for the responses, 0 for one that gave none or no number."""
    return sum(_cost(m) for m in messages)


def web_searches(messages: Sequence[ModelMessage]) -> int:
    """The web searches OpenRouter ran for the responses."""
    total = 0
    for message in messages:
        usage: dict[str, Any] = _details(message).get("server_tool_use") or {}
        total += int(usage.get("web_search_requests") or 0)
    return total
