import json
from typing import Any

from openai import APIError, APITimeoutError, OpenAI

from app.llm.base import LLMInterpreter, LLMInterpreterError
from app.llm.prompt import SYSTEM_PROMPT, build_user_prompt


class OpenRouterInterpreter(LLMInterpreter):
    """LLM interpreter backed by OpenRouter's OpenAI-compatible chat API.

    OpenRouter is a router in front of many underlying models, so this class
    never hardcodes a model family -- the model slug is just config
    (OPENROUTER_MODEL). Swapping models, or swapping to a different
    OpenAI-compatible endpoint entirely, only needs env var changes; see
    app/llm/factory.py for swapping the provider class itself.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_tokens: int = 1024,
        site_url: str = "",
        site_name: str = "",
    ) -> None:
        # Deliberately does not raise on missing api_key/model: construction
        # happens during FastAPI dependency resolution, before our
        # LLMInterpreterError degrade/error handling is in scope. Missing
        # config instead surfaces as a normal LLMInterpreterError from
        # interpret(), so GET /health and misconfigured-but-degraded
        # POST /optimize-energy responses both stay controlled.
        self._model = model
        self._max_tokens = max_tokens
        self._configured = bool(api_key) and bool(model)
        self._client = OpenAI(api_key=api_key or "unset", base_url=base_url, timeout=timeout_seconds)
        self._extra_headers = {}
        if site_url:
            self._extra_headers["HTTP-Referer"] = site_url
        if site_name:
            self._extra_headers["X-Title"] = site_name

    def interpret(self, operator_notes: list[str]) -> Any:
        if not self._configured:
            raise LLMInterpreterError("OPENROUTER_API_KEY / OPENROUTER_MODEL is not configured")
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                max_tokens=self._max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(operator_notes)},
                ],
                extra_headers=self._extra_headers or None,
            )
        except (APITimeoutError, APIError) as exc:
            raise LLMInterpreterError(f"OpenRouter call failed: {exc}") from exc
        except Exception as exc:  # transport/network errors from httpx etc.
            raise LLMInterpreterError(f"OpenRouter call failed: {exc}") from exc

        choices = getattr(response, "choices", None)
        if not choices:
            raise LLMInterpreterError("OpenRouter returned no choices")

        content = choices[0].message.content
        if not content:
            raise LLMInterpreterError("OpenRouter returned empty content")

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMInterpreterError(f"OpenRouter returned non-JSON content: {exc}") from exc
