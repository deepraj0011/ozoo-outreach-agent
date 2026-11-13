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
