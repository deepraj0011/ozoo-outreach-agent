"""Anthropic Messages API adapter: a forced tool call via tool_choice."""
from __future__ import annotations

from typing import Any, Dict, List

import anthropic

from .base import LLMBadOutput, LLMError, LLMTruncated, LLMUnavailable, status_error


class AnthropicLLM:
    def __init__(self, client: Any) -> None:
        self._client = client  # anthropic.Anthropic, with timeout and max_retries set by the factory

    def structured(self, *, system: str, messages: List[Dict[str, str]], tool_name: str,
                   tool_description: str, schema: Dict[str, Any], max_tokens: int,
                   model: str) -> Dict[str, Any]:
        try:
            resp = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                tools=[{"name": tool_name, "description": tool_description, "input_schema": schema}],
                tool_choice={"type": "tool", "name": tool_name},
                messages=messages,
            )
        except anthropic.APITimeoutError:
            raise LLMUnavailable("llm unavailable: timeout") from None
        except anthropic.APIConnectionError:
            raise LLMUnavailable("llm unavailable: connection error") from None
        except anthropic.APIStatusError as exc:
            raise status_error(exc.status_code) from None
        except anthropic.AnthropicError as exc:
            raise LLMError(f"llm error: {type(exc).__name__}") from None

        stop = getattr(resp, "stop_reason", None)
        if stop == "max_tokens":
            raise LLMTruncated("llm output truncated")
        if stop == "refusal":
            raise LLMBadOutput("llm refused")
        block = next((b for b in (getattr(resp, "content", None) or [])
                      if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None) == tool_name), None)
        if block is None:
            raise LLMBadOutput("llm returned no tool call")
        if not isinstance(block.input, dict):
            raise LLMBadOutput("llm tool arguments are not an object")
        return block.input
