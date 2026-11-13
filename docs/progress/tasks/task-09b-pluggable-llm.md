# Task 9b: Pluggable AI model provider

## Status
✅ Done after 1 fix round. The controller verified the fixes. Commits: `9630116`, `1929218`. Tests: 959 → 1126.

## What it does (plain language)
The company chooses the AI model, not the code. Reading reply intent (the classifier) and answering WhatsApp questions (the chatbot) both go through one connection point (`LLMClient`). The provider is picked in settings:

| `LLM_PROVIDER` | Use for |
|---|---|
| `anthropic` | Claude |
| `openai` | OpenAI GPT models |
| `azure_openai` | OpenAI models on the company's Azure (`LLM_BASE_URL` = Azure endpoint, `AZURE_OPENAI_API_VERSION`; model = deployment name) |
| `openai_compatible` | Any OpenAI-format API at `LLM_BASE_URL`: Gemini (OpenAI endpoint), Mistral, Groq, OpenRouter, self-hosted Ollama/vLLM |

Models are set separately: `LLM_CLASSIFIER_MODEL` and `LLM_CHAT_MODEL`. `LLM_TIMEOUT_S` defaults to 30.

## Files
- `src/outreach/llm/`:
  - `base.py`: the port and the error types.
  - `anthropic_provider.py`
  - `openai_provider.py`
  - `factory.py`: `make_llm`.
  - `fake.py`: for tests.
- `classifier.py` and `chatbot.py` now contain no vendor names.
- Also changed: `config.py`, `.env.example`, `tests/conftest.py` (network guard), `tests/llm_http.py`, `tests/test_llm_*.py`.

## Safety (unchanged whatever the model)
- **Any model failure goes to a human.** This covers a timeout, an error, a cut-off answer, a refusal, or malformed output. The classifier returns "unsure" and the chatbot hands off.
- **Existing checks still run.** Prompt-injection neutralising, the price and link post-checks, and referral grounding all stay in place.
- **No secrets leak.** The API key, base URL, prompts and lead text never appear in logs or error messages, and the SDK loggers are capped.
- **The base URL is restricted.**
  - Plain http is allowed only for a self-hosted model on a private network.
  - Cloud metadata and link-local addresses are always rejected.
- **The answer format is portable.** It works across providers: no type arrays, and optional fields are simply left out.
- **Newer OpenAI models are handled.** `openai` and `azure_openai` send `max_completion_tokens`, which those models require.
- **Tests can't touch the network.** An autouse guard blocks real network calls.

## Depends on / used by
- **Depends on:** Task 9 (classifier) and Task 10 (chatbot).
- **Used by:** Task 12a, which runs these calls in the background.

## Open items
- **Data terms (go-live).** Check the chosen provider's data-processing terms and data location, because lead reply text is sent to it.
- **Gemini.** If Gemini is chosen, check that its OpenAI-compatible endpoint accepts forced function calls.
- **Reasoning models.** On these, the token budget (400 classifier / 500 chat) also covers the model's reasoning tokens. Raise it if replies come back truncated (they go to "unsure").
