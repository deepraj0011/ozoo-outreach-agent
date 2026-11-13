# Task 06: Email adapter, webhooks, click redirect, executor

## Status
✅ Done. Review clean after 2 fix rounds (`a521f9b`, `4cb3162`).

## What it does (plain language)
Connects the pure rules to the real world. It talks to Instantly, receives its webhooks, handles tracked-link clicks and the unsubscribe page, accepts leads over an API, runs the actions the rules return, and posts Slack alerts after the database commit.

## Files
- `src/outreach/http.py`, `emailtool/base.py`, `emailtool/fake.py`, `emailtool/instantly.py`
- `src/outreach/alerts.py`, `ingest.py`, `app.py`, `unsubscribe_page.py`
- `src/outreach/actions.py` (executor), `db.py` (SQLite fix), `models.py` (`Alert.posted_at`)
- `tests/test_http.py`, `test_tool_adapter.py`, `test_execute.py`, `test_webhooks_email.py`, `test_redirect.py`, `test_intake_api.py`, `test_alerts.py`, `tests/fakes.py`

## Key functions and interfaces
- `request_with_retry(client, method, url, *, idempotent=True, ...)`.
- `EmailTool` protocol: `add_lead`, `update_variables`, `pause`, `resume`, `stop`, `reply`, `parse_webhook`, `mailbox_stats`. `NormalizedEvent`, `BadSignature`, `ToolUnsupported`.
- `InstantlyTool`, `parse_instantly_webhook`.
- `Deps` with ports `WaPort`, `OutreachPort`, `Alerter` and optional `classify`, `bot_reply`, `handoff`. `execute(session, lead, actions, deps, now, event=None)`.
- `ingest(session, events, deps, now, scanners)`, `match_lead`.
- `SlackAlerter.send(kind, text, lead, now, session=None)`, `unposted_alert_ids`.
- `create_app(settings, deps, engine)`. Routes: `POST /webhooks/email`, `GET /r/{token}`, `GET|POST /u/{token}`, `POST /intake`, `GET /healthz`.

## Depends on
Tasks 1 (fixtures, settings), 2 (models, states), 3 (`intake_lead`), 4 (links, bot filter, score), 5 (actions, `decide`).

## Used by
Task 7 (`EmailTool`, `OutreachPort.refresh_next_step`), Task 8 (`ingest`, `NormalizedEvent`, `request_with_retry`), Task 9 (`Classify` hook), Task 10 (`BotReply`, `handoff` hooks), Task 11 (`ingest`), Task 12 (real ports, unposted alerts, click settling), Task 13 (deploy `app`).

## Key decisions (from the rulings)
- Instantly per-lead pause/resume is not implemented: it raises and becomes an `integration_error` alert asking for a manual pause.
- `execute()` uses a `Deps` object of Protocols so later tasks plug in real implementations.
- `POST /intake` needs `X-API-Key`.
- Unsubscribe is scanner-safe (GET redirects to a confirm page; POST opts out; RFC 8058 one-click accepted).
- Slack posts after commit; unmatched-sender alerts may include the sender address in Slack but never in logs.
- Production hardening: constant-time compares, timeouts, 1 MiB body cap, no open redirect, no PII in logs.
- Fix round 1: domain-matched replies keep the real sender in the event; non-idempotent POSTs (add lead, reply) retry only on connect errors, 429, 503; step counter only moves forward; unmatched alert failure is isolated.

## Tests
110: http 10, tool adapter 25, execute 24, webhooks_email 25, redirect 13, intake_api 5, alerts 8. (100 in the first commits, 10 in fix round 1, 4 in fix round 2 — Slack posts only after the outermost DB commit.)

## Commits
`b01edd6` feat: http retry helper, Instantly email adapter, Slack alerter. `b615b84` feat: action executor with Deps ports and integration-error isolation. `8eecca1` feat: email adapter, shared ingest, click redirect, action executor. `a521f9b` fix: keep real sender on domain match, safe POST retries, Slack after commit. `4cb3162` fix: post Slack only after outermost commit.

## Open items / deferred minors
- Uvicorn access logs record `/r` and `/u` tokens. Task 13 runbook must disable or redact the access log and note X-Forwarded-For trust.
- Event-id collision when the tool sends no timestamp (go-live checklist).
- `test_webhook_oversized_413` accepts 400 or 413.
- Carry-forward for Task 9: a domain-matched reply classified as `unsubscribe` must suppress only `data.sender_email` (and alert), not the original lead. Interested or question from a colleague alerts sales with the sender.
- Carry-forward for Task 12: worker must retry Slack for alerts with `posted_at` NULL and send a digest of `integration_error` alerts.
- Slack post runs in the request thread after commit; a slow Slack delays the webhook response (up to about 30 s).
- `posted_at` is written through `session.get_bind()` (needs rework if a session is bound to a Connection).
- No migration tool; `create_all` only (new `Alert.posted_at` column).
- Ports must never call `session.commit()`.
