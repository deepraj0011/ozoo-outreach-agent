"""The LLM port: one structured (forced tool / function call) request, and the errors every
provider raises.

Error strings are fixed text written here (a reason and at most an HTTP status code). They never
contain the prompt, message text, API key, base URL or the provider's own error message (an
OpenAI 401 body echoes part of the key).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Protocol, runtime_checkable

# The SDKs log request options (prompts) and URLs at DEBUG/INFO; keep them at WARNING, like
# httpx/httpcore in outreach.http.
for _name in ("anthropic", "openai", "httpx2", "httpcore2"):
    logging.getLogger(_name).setLevel(logging.WARNING)


class LLMError(Exception):
    """Base of every provider error. str() holds no prompt, message text or key."""


class LLMTruncated(LLMError):
    """The output hit max_tokens / length before the tool call was complete."""


class LLMBadOutput(LLMError):
    """No tool call, a refusal, or arguments that are not a JSON object."""


class LLMUnavailable(LLMError):
    """Timeout, connection error, 429/5xx after the SDK's retries, or an auth error."""


@runtime_checkable
class LLMClient(Protocol):
    def structured(self, *, system: str, messages: List[Dict[str, str]], tool_name: str,
                   tool_description: str, schema: Dict[str, Any], max_tokens: int,
                   model: str) -> Dict[str, Any]:
        """Force one call of `tool_name` and return its arguments as a dict.

        `messages` are {"role": "user"|"assistant", "content": str}. Raises LLMError subclasses.
        """
        ...


def status_error(status: int) -> LLMError:
    """Map an HTTP status left after the SDK's retries to our error (status code only)."""
    if status in (401, 403, 408, 409, 429) or status >= 500:
        return LLMUnavailable(f"llm unavailable: http {status}")
    return LLMError(f"llm request rejected: http {status}")
