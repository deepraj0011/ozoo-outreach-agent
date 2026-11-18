# 1. What is this repo?

> Read this first. It takes about 10 minutes. Next: [2. How it works](02-how-it-works.md).

## In one sentence

An **automatic sales assistant** for Ozoo.ai. It takes a list of B2B leads and:
1. contacts each one by **email** and/or **WhatsApp**;
2. follows up on its own;
3. reads the replies with **AI**;
4. books demos on **Cal.com**;
5. tells the sales team in **Microsoft Teams** when someone is interested.

Finding leads is **not** part of this repo. We start when a lead is handed to us.

**What we sell.** Ozoo.ai is Habilelabs' conversation-AI product line for Indian lenders and insurers (banks, NBFCs, housing finance, insurers, collections): Post-Call Analytics (call quality and compliance), Voice AI agents and WhatsApp/chat agents. The leads are people at those companies. What the WhatsApp chatbot knows about Ozoo.ai is in `kb/ozoo_kb.md`.

```
 Leads (CSV / Excel / API)
          │
          ▼
 ┌──────────────────────────────────────────────────────────┐
 │                  OUTREACH AGENT (this repo)              │
 │                                                          │
 │  check address ─► send email / WhatsApp ─► follow up     │
 │        ▲                                      │          │
 │        │       replies, clicks, bookings ◄────┘          │
 │        │                │                                │
 │        │           AI reads replies                      │
 │        │                │                                │
 │        └──── decide next step (rules) ───► Teams alert   │
 └──────────────────────────────────────────────────────────┘
          │                    │                  │
     Our mailboxes       WhatsApp (Meta)       Cal.com
```

## Technology (what each piece is, simply)

| Piece | Technology | Plain meaning |
|---|---|---|
| Main program | **Python 3.10 + FastAPI** | The "brain". It receives updates from outside services and decides what to do. |
| Storage | **SQLAlchemy** with **SQLite** (local) / **PostgreSQL** (production) | Remembers every lead, message, click and reply. |
| Timer | **Worker** (`worker.py`) | Wakes up every few seconds: "Who is due a follow-up? Did anyone wait too long?" |
| Sending email | **SMTP** (standard email sending) from our own Google Workspace / Zoho mailboxes | Same as pressing "Send" in Gmail, done by code. No paid email tool needed. |
| Reading email | **IMAP IDLE** | Keeps each inbox open live, so a reply is noticed within seconds. |
| Address check | **Reacher** (free, open source) or ZeroBounce | Checks an address exists before we send to it. |
| WhatsApp | **WhatsApp Business Cloud API** (Meta, official) | Approved templates, buttons, chat. |
| AI | Any LLM: **Anthropic, OpenAI, Azure OpenAI, or self-hosted** | Classifies replies and runs the WhatsApp chatbot. Changed by a setting, not by code. |
| Booking | **Cal.com** webhooks | Tells us when a demo is booked, moved or cancelled. |
| Alerts | **Microsoft Teams** (webhook + `@OzooAgent` commands) | Posts a card to the sales channel. Sales can also reply with commands. |
| Tests | **pytest**: **1,654 tests** | Every rule and every tracking scenario is tested automatically. Real network calls are blocked in tests. |

## Folder map

```
src/outreach/            ← all the code
  app.py                 ← web server: webhooks, tracked links, open image, unsubscribe page
  ingest.py              ← every incoming event goes through here (one pipeline)
  rules.py               ← THE DECISIONS: "event X happened to lead Y → do Z" (pure, no I/O)
  actions.py             ← carries out the decisions (send, alert, change state)
  states.py              ← the lead's life stages and which moves are allowed
  worker.py              ← time-based jobs (follow-ups, reminders, settling clicks)
  botfilter.py           ← tells a real person from a robot/scanner
  links.py               ← signed tracked links (/r/), open image (/o/), unsubscribe (/u/)
  sequencer.py           ← the 4-email schedule (days 0, 4, 9, 15)
  classifier.py          ← AI reads a reply and labels it
  chatbot.py             ← WhatsApp AI chatbot
  emailtool/             ← email senders: mailbox (ours), gmail, instantly, mailpit (local), fake
  whatsapp/              ← WhatsApp client, sender rules, webhook parser
  llm/                   ← AI provider adapters (one interface, many providers)
  demo_console.*         ← local web screen: import leads, see status, take over chats
config/scanners.yaml     ← known scanner / privacy-proxy IP ranges
templates/               ← email + WhatsApp wording (marketing edits these, not code)
kb/ozoo_kb.md            ← what the chatbot knows about Ozoo.ai
tests/                   ← 1,654 automated tests
docs/                    ← see docs/README.md: onboarding, guides, plan, reference, progress
scripts/                 ← local_setup, run_local, start_demo
```

## Run it on your laptop (5 minutes, nothing real is sent)

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q              # expect: 1654 passed
scripts/local_setup              # creates .env.local with fake services + local.db
scripts/run_local                # app on http://127.0.0.1:8000
```

Open http://127.0.0.1:8000/, import `docs/samples/sample_leads.xlsx` and use the "simulate" buttons on a lead:
- reply
- click
- bounce
- book
- WhatsApp tap

In this mode **every outside service is fake**, so nothing leaves your machine. The **Conversations** page shows each WhatsApp chat as the lead's phone would show it (template text and buttons), including a preview before anything is sent.

Every outside service has a setting with a fake and a real version:

| Setting | Options |
|---|---|
| `EMAIL_MODE` | `fake` · `mailpit` (local test inbox) · `gmail` (one free mailbox) · `mailbox` (our production sender) · `real` (Instantly) |
| `WHATSAPP_MODE` | `fake` · `real` |
| `LLM_MODE` | `fake` · `real` (provider chosen by `LLM_PROVIDER`) |
| `ALERTS_MODE` | `fake` · `real` |
| `VERIFY_MODE` | `fake` · `real` (`VERIFY_PROVIDER=reacher` or `zerobounce`) |

Fake modes are **refused** unless `APP_ENV=local`, so a fake can never run in production by mistake.

## Where a reviewer should look first

1. `src/outreach/rules.py` + `tests/test_rules.py`: all business decisions, as plain functions.
2. `src/outreach/ingest.py`: the single path every event takes.
3. `src/outreach/botfilter.py` + `worker.py` (`_process_settled_clicks`): robot detection.
4. `src/outreach/emailtool/mailbox.py`: our own email sender and inbox watcher.
5. `docs/progress/README.md`: what was built task by task. `docs/progress/decisions.md`: why.

## Built-in safety

- **Secrets are never in the code.** They live in `.env` files, which git ignores. Signatures are checked in constant time on every webhook.
- **Logs never contain** emails, phone numbers, message text or link codes.
- **No double sending.** Every send is guarded, so a crash or restart never emails anyone twice.
- **One lead, one record.** Email and WhatsApp share the same state, so a reply on one channel stops the other.
- **Unsubscribe is permanent** across both channels. It's also safe from scanners: just opening the link only shows a confirm button.
- **EU leads are held** until the GDPR sign-off setting is turned on.

## What is NOT done yet

See `docs/progress/go-live-checklist.md`. The main items:
- a server and deployment;
- a sending domain and mailbox warm-up;
- Meta template approval;
- final marketing wording;
- database migrations (Alembic) before production.
