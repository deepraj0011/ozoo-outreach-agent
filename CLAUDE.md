# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Ozoo.ai itself is Habilelabs' conversation-AI product line for Indian lenders (banks, NBFCs, housing finance, insurers): Post-Call Analytics, Voice AI agents, WhatsApp/chat agents. Messaging and the KB must use that positioning, not "outreach automation".

This repo is the Ozoo.ai outreach agent: takes a lead from intake (lead sourcing is out of scope) through email (4-step sequence sent by a cold-email tool, Instantly) and/or WhatsApp (opt-in template with two quick-reply buttons, reminder, chatbot), classifies replies with an LLM, books demos via Cal.com and alerts sales in Microsoft Teams. Both channels share **one lead record and one state**. Docs index: `docs/README.md` (onboarding/, guides/, plan/, reference/, progress/). Spec: `docs/plan/outreach-agent-plan-v2.md`. Progress board: `docs/progress/README.md`; binding rulings with their reasons: `docs/progress/decisions.md`; pre-live gaps: `docs/progress/go-live-checklist.md`.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # first time
.venv/bin/pytest -q                                           # full suite (network is blocked in tests)
.venv/bin/pytest tests/test_rules.py -q                       # one file
.venv/bin/pytest tests/test_rules.py -k "reply and booking"   # by name

scripts/local_setup            # writes .env.local (fresh secrets, every service fake) + local.db
scripts/run_local              # FastAPI app + in-process worker (10 s tick) on 127.0.0.1:8000
scripts/start_demo [.env.demo] # run_local + Mailpit + opens the web app at /

# dev CLI (refuses unless APP_ENV=local); reads OUTREACH_ENV_FILE / .env.local
.venv/bin/python -m outreach.dev seed | show [ID] [--mask] | tick | advance --hours 48 | advance --reset
.venv/bin/python -m outreach.dev sim SCENARIO --lead ID   # signed webhook to the running server
.venv/bin/python -m outreach.world [--leads 100]          # realistic lead simulation, own temp dir (no env file)
```

No linter/formatter is configured. Code must run on **Python 3.10** (use `class X(str, Enum)`, not `StrEnum`). Schema is created with `Base.metadata.create_all` — there are no migrations yet.

## Architecture

Package is `src/outreach/` (pytest adds `src` to the path; tests import `outreach.*` and `tests.fakes`).

**Event pipeline** (`ingest.py`), used by every webhook and by the worker:
webhook route in `app.py` (1 MiB cap, constant-time signature/secret check) → adapter normalises to `NormalizedEvent` → insert event in a savepoint (duplicate `provider_event_id` skipped, so provider retries are safe) → match lead (lead_id, tool id, email, phone, then company domain for replies) → `botfilter.py` verdict (only `human` events change the flow) → **`rules.decide()`** → **`actions.execute()`** → commit → Teams alerts posted only after commit.

- `rules.decide()` is **pure**: lead/event snapshots in, ordered list of action dataclasses out. No DB, no I/O. Most behaviour changes belong here and are tested in `test_rules.py`.
- `actions.execute()` applies actions. State changes go only through `states.transition()` (allowed-transition table in `states.py`). External calls go through the `Deps` ports (Protocols in `actions.py`); a failing port becomes an `integration_error` row and execution continues.
- Clicks (and opens, and `visit` events) are stored unprocessed and decided by the worker after a 30 s settle window (to catch scanner bursts). Unsubscribe GET only shows a confirm page; the POST opts out.
- Verified visits (`visits.py`): clicks to our own site pages forward with `?oz_v=<signed token>`; the site's `/v.js` snippet POSTs `/v/<token>` once a person has actually read the page. With `CLICK_ALERTS_NEED_CHECK` (default on), clicks alone never alert sales (`click_check` instead) nor trigger an email to the lead; a verified visit, reply or booking does.
- Our own sender (`EMAIL_MODE=mailbox`/`gmail`) sends only inside `sendwindow.py` slots (working hours, follow-ups rotate afternoon/morning); replies to a person go at once.
- LLM calls (classifier, chatbot) run as background jobs (`jobs.py`), not inside webhook requests.

**Worker** (`worker.py`): `tick()` runs ordered time-driven jobs (start new leads, settled clicks, WhatsApp reminder/no-response, holds, end of sequence, SLA, score decay, due LLM jobs, alert retries, mailbox health, stuck leads…), each in its own transaction, per-lead commits, re-loading each lead with a row lock and re-checking the selection predicate. Every send/alert is guarded by a timestamp or flag written in the same transaction so a repeated tick at the same `now` is idempotent. Only one worker ticks at a time (Postgres advisory lock or `worker_lock` lease row). Locally it runs inside the app (`RUN_WORKER=1`) — never also run `python -m outreach.worker --loop` against the same SQLite DB.

**Swappable services via `*_MODE` settings** (`config.py`): `EMAIL_MODE` (`fake` | `real` Instantly | `mailpit` | `gmail` | `mailbox` = own SMTP/IMAP mailboxes with daily caps, `emailtool/mailbox.py`), `WHATSAPP_MODE`, `LLM_MODE`, `ALERTS_MODE`, `VERIFY_MODE` (`fake` | `real`; real uses ZeroBounce or self-hosted Reacher via `VERIFY_PROVIDER`). Fakes live next to each adapter (`emailtool/fake.py`, `whatsapp/fake.py`, `llm/fake.py`, `llm/local_fake.py`). `mailpit`/`gmail` modes also start `outreach.mailpit_sender` (started by `run_local`). Settings refuse fake modes and `TIME_SCALE` unless `APP_ENV=local`, and refuse fast clocks/short delays when real sending is on locally unless `ALLOW_REAL_LOCAL=1`.

**LLM is provider-agnostic**: classifier and chatbot depend only on the port in `llm/base.py`; `llm/factory.py` picks `anthropic`, `openai`, `azure_openai` or `openai_compatible` from `LLM_PROVIDER` + model settings. Don't hard-wire a provider.

**Content/config outside code**: email copy `templates/emails.yaml` (every step also renders a letter-like designed HTML part via `richmail.py`; images in `static/email/`, served at `/img/`) plus web-app templates in the `email_template` table (`template_library.py`, Templates screen; approved versions join the YAML ones through `templates_for(session)`), WhatsApp templates `templates/whatsapp.yaml`, chatbot knowledge base `kb/ozoo_kb.md`, scanner IP ranges `config/scanners.yaml`.
- The chatbot answers only from `kb/ozoo_kb.md`; its post-check rejects any reply with a URL or a currency amount not written in the KB (the KB deliberately has no amounts).
- WhatsApp template bodies live in Meta; `templates/whatsapp.yaml` `preview` is a display-only copy for the web app and must match the approved Meta text.

**Phone numbers** (`channels.normalize_phone`): `+`/`00` numbers keep their country; otherwise the lead's country (ISO code or name, `eu.country_code`), else `DEFAULT_PHONE_REGION` (default `IN`). Stored as E.164.

**Realistic simulation** (`world/`): `python -m outreach.world` runs persona leads (most never read; Apple / Defender / bank-sandbox inbox machines; late replies) through the real app, worker and sender in a temp dir with a stand-in SMTP, and reports agent outcome vs truth. `tests/test_world.py` runs a subset; keep every story persona's outcome as expected when changing rules.

**Local web app** (React + MUI in `frontend/`, built by `scripts/build_web` into `src/outreach/webapp/`, which is gitignored; `scripts/run_local` builds it when missing or stale; served at `/app` by `web_app.py`, `/` redirects there, local only; `/demo` still serves the older `demo_console.html` page): Campaign board, Leads (CSV/Excel import), Conversations (a placeholder that links to the /demo page, where takeover lives), Templates, Connectors (status only), Settings (read-only). Frontend helper tests: `cd frontend && npm test`. Fake clock: `localclock.py` (`dev advance`).

## Tests

`tests/conftest.py` provides an in-memory SQLite `session` fixture, `make_lead(...)`, fixed `NOW` (2026-11-12 10:00 UTC), and an autouse fixture that blocks real HTTP. Fakes for ports are in `tests/fakes.py`; provider payload fixtures in `tests/fixtures/`. `test_local_e2e.py` runs the full local flow.

## Rules for working here

- **Never modify or delete the user's local runtime files** (`local.db*`, `demo.db*`, `.env.local`, `.env.demo`, `.gmail_tool.db`, `.mailpit_tool.db`). Smoke-test the app in an isolated copy/temp directory with its own env file and DB.
- After finishing a task, update `docs/progress/` (status board row in `README.md`, test count, and a `tasks/task-NN-*.md` note; add new rulings to `decisions.md`).
- Logs must never contain emails, phone numbers, message text or tracked-link tokens (only `dev show` prints PII; `--mask` hides it).
- `docs/test_leads.csv` contains real addresses and is gitignored — never commit it. New docs go in the matching `docs/` subfolder and get a line in `docs/README.md`.
- Other Claude sessions may work in this repo at the same time: stage files explicitly by path (never `git add -A`), and leave others' uncommitted files alone.
