# Architecture

## Module map

All modules below exist (tasks 1-17).

```mermaid
flowchart TD
    subgraph SRC[Sources]
        LS[Lead source] -->|POST /intake| APP
        EW[Email tool webhooks] -->|POST /webhooks/email| APP
        CL[Link clicks and unsubscribe page] -->|GET /r, GET+POST /u| APP
        WAW[Meta WhatsApp webhooks] -->|/webhooks/whatsapp| APP
        CAW[Cal.com webhooks] -->|/webhooks/cal| APP
        TMC[Teams outgoing webhook] -->|/teams/commands| APP
    end
    APP[app.py routes] --> INTAKE[intake.py + verify.py + channels.py]
    APP --> INGEST[ingest.py]
    INGEST --> BOT[botfilter.py + score.py]
    INGEST --> RULES[rules.py decide: pure]
    RULES --> ACTIONS[actions.py execute]
    ACTIONS --> STATES[states.py transition]
    STATES --> DB[(db.py + models.py)]
    ACTIONS --> PORTS{{Deps ports}}
    PORTS --> EMAIL[emailtool: mailbox / Instantly / Gmail / Mailpit]
    PORTS --> TEAMS[alerts.py TeamsAlerter]
    PORTS --> WA[WhatsApp client]
    PORTS --> CLS[AI classifier]
    PORTS --> CHAT[AI chatbot + handoff]
    PORTS --> OUT[outreach.py starter/switcher]
    PORTS --> CAL[Cal.com booking]
    SEQ[sequencer.py + templates] --> EMAIL
    WORKER[worker.py tick + health.py] --> OUT
    WORKER --> INGEST
```

## How an event flows

1. **Webhook arrives** (for example an email reply). The route checks size (1 MiB cap) and the signature or shared secret. Bad secret gives 401.
2. **Adapter normalises** the payload into `NormalizedEvent` (kind, channel, lead hints, provider event id).
3. **Ingest** inserts the event inside a savepoint. A repeated `provider_event_id` is skipped, so retries are safe.
4. **Match lead:** by `lead_id`, tool lead id, email, phone, then (replies only) company domain. No match gives an `unmatched_*` alert.
5. **Verdict:** bot filter marks opens/clicks `human`, `bot` or `machine`. Only `human` events change the flow.
6. **Decide:** `rules.decide()` is pure. It takes snapshots of lead and event and returns an ordered list of actions. No DB, no I/O.
7. **Execute:** `actions.execute()` applies each action. State changes go through `states.transition()`. External calls (email tool, WhatsApp, Teams) go through `Deps` ports. A failing port becomes an `integration_error` row and the rest continues.
8. **Commit:** ingest owns the commit. Any error rolls back and returns 500 so the provider retries.
9. **Teams after commit:** alerts are written as rows during step 7, but posted to Microsoft Teams (Workflows webhook, Adaptive Card) only after the commit succeeds. Failed posts keep `posted_at` empty for a worker retry (task 12).

Clicks are stored unprocessed and decided later by the worker (30 s settle window, so two-link scanner bursts can be spotted). The unsubscribe click is the exception: the GET only redirects to a confirm page, and the POST opts the lead out.

## Module table

| Module | Responsibility | Built in | Exists |
|---|---|---|---|
| `config.py` | Settings from env, secret strength checks | Task 1 (hardened in 4) | yes |
| `db.py` | Engine, session, UTC datetime type, SQLite transaction fix | Task 2 (fix in 6) | yes |
| `models.py` | Lead, Event, ChatMessage, Suppression, Alert | Task 2 | yes |
| `states.py` | LeadState, allowed transitions, `transition()` | Task 2 | yes |
| `verify.py` | ZeroBounce email verifier | Task 3 | yes |
| `channels.py` | Phone normalising, `choose_channel`, `fallback_channel` | Task 3 | yes |
| `intake.py` | Intake gate: validate, suppress, dedup, account hold, EU hold | Task 3 (review update) | yes |
| `eu.py` | EU / EEA detection (country, phone region, email domain) | Review update | yes |
| `holds.py` | When paused colleagues restart; release EU holds once approved (called by the Task 12 tick) | Review update | yes |
| `links.py` | Signed tracked-link tokens | Task 4 | yes |
| `botfilter.py` | Classify events as human / bot / machine | Task 4 | yes |
| `score.py` | Engagement points, decay, bands | Task 4 (pure `points_for` in 5) | yes |
| `actions.py` | Action dataclasses, `Deps` ports, `execute()` | Task 5 (dataclasses), 6 (executor) | yes |
| `rules.py` | `decide()`: events to actions, both channels | Task 5 | yes |
| `http.py` | Retrying HTTP helper with timeouts | Task 6 | yes |
| `emailtool/base.py`, `fake.py`, `instantly.py` | Email tool port, test fake, Instantly adapter | Task 6 | yes |
| `alerts.py` | Alert rows plus Teams post after commit; `teams_escape` makes third-party text inert | Task 6 | yes |
| `ingest.py` | Shared event pipeline: dedup, match, verdict, decide, execute, commit | Task 6 | yes |
| `app.py` | FastAPI: `/webhooks/email`, `/r`, `/u`, `/intake`, `/healthz` | Task 6 | yes |
| `unsubscribe_page.py` | Confirm and success HTML for `/u/{token}` | Task 6 | yes |
| `sequencer.py`, `templates/emails.yaml` | Email enroll, variants, end-of-sequence routing | Task 7 | yes |
| `whatsapp/client.py`, `whatsapp/webhook.py` | Cloud API client and webhook parser | Task 8 | yes |
| `classifier.py` | AI reply classification (provider via `llm/`) | Task 9 | yes |
| `chatbot.py`, `handoff.py` | WhatsApp bot, Teams takeover (`/teams/commands`) | Task 10, 10b | yes |
| `booking.py` | Cal.com events, fit routing, handoff summary | Task 11 | yes |
| `outreach.py`, `worker.py`, `health.py` | Start/switch channel, 60 s tick, mailbox health | Task 12 | yes |
| `scripts/`, `docs/guides/local-testing.md`, `test_local_e2e.py` | Run locally + local end-to-end test (deploy deferred) | Task 13 | yes |
| `llm/` | One AI port; Anthropic, OpenAI, Azure OpenAI, OpenAI-compatible adapters | Task 9b | yes |
| `jobs.py` | Background AI jobs (classify, bot reply) outside webhook requests | Task 12a | yes |
| `emailtool/mailbox.py`, `mailpit_sender.py` | Our own SMTP / IMAP sender with caps and warm-up; the sender loop | Tasks 14, 17 | yes |
| `demo_console.py`, `demo_console.html` | Local web app at `/` | D3, 16 | yes |
