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

Role = Literal["orchestrator", "general", "math", "writer", "curator", "reader"]

FLASH = "deepseek/deepseek-v4.1-flash"
# Open weights, sees images, built for OCR of text and math, and an instruct model: no thinking to
# pay for. $0.15 / $0.60 per million input / output tokens.
READER = "qwen/qwen3-vl-30b-a3b-instruct"
ROUTING: OpenRouterProviderConfig = {"sort": "price"}  # the cheapest provider of the model
MODEL_TIMEOUT_SECONDS = 60  # one request to the model


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    reasoning_tokens: int  # bounds the thinking before the reply
    max_output_tokens: int  # reasoning and reply together, so above the reasoning budget
    timeout_seconds: int = MODEL_TIMEOUT_SECONDS  # one request


ROLE_MODELS: dict[Role, ModelSpec] = {
    "orchestrator": ModelSpec(FLASH, 2_000, 4_000),
    "general": ModelSpec(FLASH, 4_000, 8_000),
    "math": ModelSpec(FLASH, 4_000, 8_000),
    "writer": ModelSpec(FLASH, 2_000, 8_000),
    # Less thinking than an answer needs: a long one ran into the output limit and lost the notes.
    "curator": ModelSpec(FLASH, 1_500, 8_000),
    # The model has no reasoning mode (0 sends none). A scanned PDF is read at OpenRouter first,
    # which can take a while.
    "reader": ModelSpec(READER, 0, 8_000, timeout_seconds=80),
}


def make_models(key: SecretStr, http_client: httpx2.AsyncClient | None = None) -> dict[Role, Model]:
    """One model per role, all on one provider and so one HTTP client."""
    provider = OpenRouterProvider(api_key=key.get_secret_value(), http_client=http_client)
    # A failed read is not retried: each try would be paid for again.
    reader_provider = OpenRouterProvider(openai_client=provider.client.with_options(max_retries=0))
    return {
        role: _make_model(reader_provider if role == "reader" else provider, spec)
        for role, spec in ROLE_MODELS.items()
    }


def _make_model(provider: OpenRouterProvider, spec: ModelSpec) -> OpenRouterModel:
    settings = OpenRouterModelSettings(
        openrouter_provider=ROUTING,
        max_tokens=spec.max_output_tokens,
        timeout=spec.timeout_seconds,
    )
    if spec.reasoning_tokens:
        settings["openrouter_reasoning"] = {"max_tokens": spec.reasoning_tokens}
    return OpenRouterModel(spec.model_id, provider=provider, settings=settings)


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
