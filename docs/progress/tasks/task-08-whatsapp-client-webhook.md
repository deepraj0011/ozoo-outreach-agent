# Task 08: WhatsApp client + webhook

## Status
⏳ To do. Not started.

## What it does (plain language)
Sends WhatsApp messages through Meta's Cloud API and receives Meta's webhooks. It enforces the 24-hour window locally: free text is refused after the window closes, before any call to Meta. Inbound texts and button taps open the window and are stored as chat messages.

## Files
Planned: `src/outreach/whatsapp/client.py`, `whatsapp/webhook.py`, `templates/whatsapp.yaml`, new routes `GET/POST /webhooks/whatsapp` in `app.py`, `tests/test_wa_client.py`, `tests/test_webhooks_wa.py`.

## Key functions and interfaces
Planned:
- `WhatsAppClient` and `FakeWhatsApp`: `send_template(phone, name, lang, variables)`, `send_text(phone, text)` (raises `WindowClosed`), `send_cta_link(phone, text, url)`.
- `parse_wa_webhook(headers, body)` verifies `X-Hub-Signature-256` and maps statuses and messages to `wa_sent/delivered/read/failed`, `wa_button`, `wa_text`.
- `GET /webhooks/whatsapp` answers Meta's verification challenge.

## Depends on
Task 1 (WhatsApp fixtures, spike notes), Task 6 (`ingest`, `NormalizedEvent`, `request_with_retry`, `WaPort`), Task 4 (links on the whatsapp channel).

## Used by
Task 10 (bot sends), Task 12 (`start_outreach`, reminders), Task 9 (WhatsApp free-text replies), Task 11 (WhatsApp booking links).

## Key decisions (from the rulings)
No rulings file yet. Ledger-wide: 10 s timeout, retry on 429/5xx, no PII in logs, signature compare in constant time. Free-form texts contain no raw URL; the booking URL is added through `make_link`.

## Tests
Not started. Planned: challenge, bad signature, each fixture, 24 h window, no HTTP outside window, duplicate status acts once, unknown number alerts without reply, template payload shape.

## Commits
None.

## Open items / deferred minors
Not started. Go-live items: template approval, real fixture capture, error codes (see [go-live-checklist.md](../go-live-checklist.md)).


---
## Update — built and reviewed (2026-11-13)
**Status:** ✅ Done, review clean after 1 fix round. Commits `f0fd2e5`, `61cdaa3`, `66b0d48`. 49 tests (`tests/test_wa_client.py`, `tests/test_webhooks_wa.py`).

**What was built:**
- `whatsapp/client.py` — `WhatsAppClient`: talks to Meta's Cloud API (templates, text, "Book demo" link button). Sends are never auto-retried after a server error, so a prospect can't get a message twice.
- `whatsapp/sender.py` — `WhatsAppSender` (the agent's WhatsApp "port"): refuses to send if the lead has no WhatsApp consent or opted out (even inside the 24 h window a STOP opens), refuses free-form messages outside the 24 h window, renders texts from `templates/whatsapp.yaml`, adds the tracked booking link, records every outgoing message.
- `whatsapp/webhook.py` — checks Meta's signature (`X-Hub-Signature-256`), turns Meta's payload into our events: sent / delivered / read / failed, button taps (Book demo / Ask a question), text messages. One broken item is skipped instead of failing the whole batch (so other people's opt-outs aren't delayed).
- Routes: `GET /webhooks/whatsapp` (Meta's setup check), `POST /webhooks/whatsapp`.

**Security:** signature checked on the raw body with a constant-time compare; no token / phone / message text in logs or errors; names containing links are replaced so they can't be used for phishing.

**Links:** uses Task 6 (`ingest`, executor, retry helper), Task 4 (tracked links), Task 5 (rules decide what to do with each WhatsApp event). Used by Task 10 (chatbot + human `/wa` replies use `send_raw_text`), Task 11 (no-show reschedule message), Task 12 (intro template + 48 h reminder).

**Open items for Task 12:** if Meta rejects a send immediately (e.g. number not on WhatsApp), Task 12 must turn that into a channel switch; mark the intro as "attempted" before sending so a timeout can't cause a duplicate intro.
