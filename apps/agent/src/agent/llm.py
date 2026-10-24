"""OpenRouter chat completions client: the only code in the agent that talks to a model.

Plain HTTP instead of the `openai` SDK: OpenRouter's extra fields (reasoning_details,
cache_control, session_id, provider, usage.cost) are first-class here instead of hiding in
`extra_body`/`model_extra`, and it needs no new dependency.
"""

import asyncio
import os
from typing import Literal, Protocol

import httpx2
from pydantic import BaseModel, Field, JsonValue, ValidationError

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class FunctionCall(BaseModel):
    name: str
    arguments: str  # JSON text written by the model, so it may be malformed


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    # Opaque reasoning blocks: they must go back to the model unchanged on the next turn.
    reasoning_details: list[dict[str, JsonValue]] | None = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0  # USD, as billed by OpenRouter


class Completion(BaseModel):
    message: Message
    usage: Usage


class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    tools: list[dict[str, JsonValue]]  # OpenAI function-calling format
    session_id: str  # one per job, keeps the prompt cache warm
    max_tokens: int


class ChatModel(Protocol):
    async def complete(self, request: ChatRequest) -> Completion: ...


class LLMError(Exception):
    """The call failed for good (after retries) or the reply was unusable."""


class _Choice(BaseModel):
    message: Message


class _Response(BaseModel):
    choices: list[_Choice] = Field(min_length=1)
    usage: Usage = Usage()


class OpenRouterClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        timeout_s: float,
        max_retries: int,
        backoff_s: float = 1.0,
        http: httpx2.AsyncClient | None = None,
    ) -> None:
        # No key is fine when an egress gateway adds it on the way out.
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._http = http or httpx2.AsyncClient(timeout=None)

    @classmethod
    def from_env(cls, *, timeout_s: float, max_retries: int) -> OpenRouterClient:
        return cls(
            os.environ.get("OPENROUTER_API_KEY"), timeout_s=timeout_s, max_retries=max_retries
        )

    async def complete(self, request: ChatRequest) -> Completion:
        body = request.model_dump(mode="json", exclude_none=True) | {
            "cache_control": {"type": "ephemeral"},
            "provider": {"zdr": True},
            "usage": {"include": True},
        }
        problem = ""
        for attempt in range(self._max_retries + 1):
            if attempt:
                await asyncio.sleep(self._backoff_s * 2 ** (attempt - 1))
            try:
                async with asyncio.timeout(self._timeout_s):
                    resp = await self._http.post(OPENROUTER_URL, json=body, headers=self._headers)
            except (TimeoutError, httpx2.TransportError) as e:
                problem = f"{type(e).__name__}: {e}"
                continue
            if resp.status_code in RETRY_STATUSES:
                problem = f"HTTP {resp.status_code}"
                continue
            if resp.is_error:
                raise LLMError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                parsed = _Response.model_validate_json(resp.content)
            except ValidationError as e:
                raise LLMError(f"malformed OpenRouter response: {e}") from e
            return Completion(message=parsed.choices[0].message, usage=parsed.usage)
        raise LLMError(f"OpenRouter failed after {self._max_retries + 1} attempts: {problem}")
