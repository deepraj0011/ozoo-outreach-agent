"""OpenAI Chat Completions adapter: a forced function call via tool_choice.

The same code serves OpenAI, Azure OpenAI (AzureOpenAI client; `model` is the deployment name)
and every OpenAI-compatible endpoint reached through `base_url` (Gemini's OpenAI-compatible
endpoint, Mistral, Groq, OpenRouter, self-hosted Ollama or vLLM). No provider "strict" mode:
optional fields stay optional and pydantic in the caller is the real validator.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import openai

from .base import LLMBadOutput, LLMError, LLMTruncated, LLMUnavailable, status_error


class OpenAILLM:
    def __init__(self, client: Any, token_param: str = "max_completion_tokens") -> None:
        self._client = client  # openai.OpenAI / openai.AzureOpenAI, timeout and retries set by the factory
        # OpenAI / Azure: max_completion_tokens (max_tokens is deprecated and rejected by reasoning
        # models; there the budget also covers reasoning tokens). Compatible servers: max_tokens.
        if token_param not in ("max_completion_tokens", "max_tokens"):
            raise ValueError("token_param must be max_completion_tokens or max_tokens")
        self._token_param = token_param

    def structured(self, *, system: str, messages: List[Dict[str, str]], tool_name: str,
                   tool_description: str, schema: Dict[str, Any], max_tokens: int,
                   model: str) -> Dict[str, Any]:
        # `raise ... from None` sets __suppress_context__: tracebacks do not print the SDK exception
        # (its message can echo part of the key or the prompt). __context__ still references it in
        # memory, so never log exc.__context__ / exc_info of these errors.
        try:
            resp = self._client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, *messages],
                tools=[{"type": "function",
                        "function": {"name": tool_name, "description": tool_description, "parameters": schema}}],
                tool_choice={"type": "function", "function": {"name": tool_name}},
                **{self._token_param: max_tokens},
            )
        except openai.APITimeoutError:
            raise LLMUnavailable("llm unavailable: timeout") from None
        except openai.APIConnectionError:
            raise LLMUnavailable("llm unavailable: connection error") from None
        except openai.APIStatusError as exc:
            raise status_error(exc.status_code) from None
        except openai.OpenAIError as exc:
            raise LLMError(f"llm error: {type(exc).__name__}") from None
        except Exception as exc:  # noqa: BLE001 - e.g. a transport error the SDK does not wrap
            raise LLMError(f"llm error: {type(exc).__name__}") from None

        choices = getattr(resp, "choices", None) or []
        if not choices:
            raise LLMBadOutput("llm returned no choices")
        choice = choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise LLMTruncated("llm output truncated")
        message = getattr(choice, "message", None)
        if getattr(message, "refusal", None):
            raise LLMBadOutput("llm refused")
        calls = getattr(message, "tool_calls", None) or []
        if not calls:
            raise LLMBadOutput("llm returned no tool call")
        fn = getattr(calls[0], "function", None)
        if getattr(fn, "name", None) != tool_name:
            raise LLMBadOutput("llm called a different tool")
        raw = getattr(fn, "arguments", None)
        if isinstance(raw, (str, bytes, bytearray)):
            try:
                raw = json.loads(raw)
            except ValueError:
                raise LLMBadOutput("llm tool arguments are not valid JSON") from None
        if not isinstance(raw, dict):
            raise LLMBadOutput("llm tool arguments are not an object")
        return raw
