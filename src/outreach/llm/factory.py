"""make_llm: build the configured LLM client. Each SDK is imported only in its own branch.

Every SDK client gets an explicit api_key and base URL (so ANTHROPIC_BASE_URL / OPENAI_BASE_URL /
OPENAI_API_KEY style env vars never redirect or re-key it), plus the configured timeout and
MAX_RETRIES (the SDK retries timeouts, connection errors, 408/409/429 and 5xx with backoff).
"""
from __future__ import annotations

from typing import Any, Optional

from .base import LLMClient, cap_sdk_loggers

MAX_RETRIES = 2
ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
# Self-hosted OpenAI-compatible servers (e.g. Ollama) may need no key; the SDK still wants one.
NO_KEY_PLACEHOLDER = "not-needed"


def make_llm(settings: Any, *, http_client: Optional[Any] = None) -> LLMClient:
    """`http_client` (an httpx2.Client) is for tests only; production uses the SDK default."""
    provider = settings.llm_provider
    common: dict = dict(timeout=float(settings.llm_timeout_s), max_retries=MAX_RETRIES)
    if http_client is not None:
        common["http_client"] = http_client

    if provider == "anthropic":
        import anthropic

        cap_sdk_loggers()

        from .anthropic_provider import AnthropicLLM

        return AnthropicLLM(anthropic.Anthropic(
            api_key=settings.llm_api_key, base_url=settings.llm_base_url or ANTHROPIC_DEFAULT_BASE_URL, **common))

    if provider == "openai":
        import openai

        cap_sdk_loggers()

        from .openai_provider import OpenAILLM

        return OpenAILLM(openai.OpenAI(
            api_key=settings.llm_api_key, base_url=settings.llm_base_url or OPENAI_DEFAULT_BASE_URL, **common))

    if provider == "azure_openai":
        import openai

        cap_sdk_loggers()

        from .openai_provider import OpenAILLM

        if not settings.llm_base_url or not settings.azure_openai_api_version:
            raise ValueError("azure_openai needs llm_base_url and azure_openai_api_version")
        return OpenAILLM(openai.AzureOpenAI(
            api_key=settings.llm_api_key, azure_endpoint=settings.llm_base_url,
            api_version=settings.azure_openai_api_version, **common))

    if provider == "openai_compatible":
        import openai

        cap_sdk_loggers()

        from .openai_provider import OpenAILLM

        if not settings.llm_base_url:
            raise ValueError("openai_compatible needs llm_base_url")
        return OpenAILLM(openai.OpenAI(
            api_key=settings.llm_api_key or NO_KEY_PLACEHOLDER, base_url=settings.llm_base_url, **common),
            token_param="max_tokens")  # most compatible servers do not know max_completion_tokens

    raise ValueError("unknown llm_provider")
