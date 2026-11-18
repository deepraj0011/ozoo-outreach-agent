# Ozoo.ai outreach agent

An automatic sales assistant for [Ozoo.ai](https://ozoo.ai), the conversation-AI product line for lenders
and insurers. It takes B2B leads and:

1. checks each lead (consent, block list, duplicates, email verification, EU hold);
2. contacts them by **email** (4-step sequence) and/or **WhatsApp** (approved template with buttons);
3. reads replies with an **AI model** (Claude, OpenAI, Azure OpenAI or any OpenAI-compatible model);
4. answers WhatsApp questions with a chatbot that only uses [`kb/ozoo_kb.md`](kb/ozoo_kb.md);
5. books demos through **Cal.com** and alerts sales in **Microsoft Teams**.

Both channels share one lead record and one state, so a reply on one stops the other. Finding leads
is out of scope: the agent starts when a lead is handed to it.

## Quick start (nothing real is sent)

Needs Python 3.10+.

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q          # the full test suite; real network calls are blocked
scripts/local_setup          # writes .env.local (fresh secrets, every service fake) and local.db
scripts/start_demo           # starts the agent and opens the web app at http://127.0.0.1:8000/
```

In the web app, import [`docs/samples/sample_leads.xlsx`](docs/samples/sample_leads.xlsx) and use the
simulate buttons to act as a lead (reply, click, tap a WhatsApp button, book).

## Documentation

Start at **[docs/README.md](docs/README.md)**. The short path:

1. [What is this repo?](docs/onboarding/01-what-is-this-repo.md), [How it works](docs/onboarding/02-how-it-works.md) and [Does tracking work?](docs/onboarding/03-tracking-explained.md)
2. [Local testing guide](docs/guides/local-testing.md)
3. [Status board](docs/progress/README.md) and [go-live checklist](docs/progress/go-live-checklist.md)

## Repository layout

```
src/outreach/     the application (FastAPI app, rules, worker, channel adapters, web app)
tests/            pytest suite
templates/        email and WhatsApp wording (edited by marketing, not code)
kb/               the chatbot's knowledge base
config/           scanner / privacy-proxy IP ranges for bot filtering
scripts/          local_setup, run_local, start_demo
docs/             onboarding, guides, plan, reference, progress
.env.example      every setting, with comments
```

## Configuration

All settings come from environment variables; [`.env.example`](.env.example) lists and explains each
one. Every outside service has a fake and a real mode (`EMAIL_MODE`, `WHATSAPP_MODE`, `LLM_MODE`,
`ALERTS_MODE`, `VERIFY_MODE`). Fake modes are refused unless `APP_ENV=local`.

Never commit `.env*` files (other than `.env.example`), databases or lead lists: `.gitignore` covers them.

## Status

Built and tested locally; not deployed yet. What remains before real sending is in the
[go-live checklist](docs/progress/go-live-checklist.md).
