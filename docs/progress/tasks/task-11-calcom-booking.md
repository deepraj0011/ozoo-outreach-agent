# Task 11: Cal.com booking + handoff

## Status
⏳ To do. Not started.

## What it does (plain language)
Receives Cal.com webhooks and moves the lead: booked, no-show, meeting held. It matches the booking to a lead by the metadata token first, then email, then phone. It scores the lead's fit from the routing-form answers. When the meeting ends it hands off to sales with a summary of both channels.

## Files
Planned: `src/outreach/booking.py`, route `POST /webhooks/cal`, `tests/test_booking.py`.

## Key functions and interfaces
Planned: `fit_from_answers(size, timeline)` (high: 50+ employees and 3 months or less; medium: 10+ or 6 months or less; else low; rule lives in config), `handle_cal_event(session, payload, deps, now)` built on `ingest`. Verifies `X-Cal-Signature-256`.

## Depends on
Task 1 (Cal.com fixtures), Task 5 (booking rules already in `decide`), Task 6 (`ingest`, `HandOff` hook).

## Used by
Task 12 (`attended_check` handoff), Task 13 (Cal.com setup).

## Key decisions (from the rulings)
No rulings file yet. Plan: duplicate webhook acts once; unmatched booking alerts sales. Reminders (24 h, 1 h) are Cal.com workflows, not code.

## Tests
Not started. Planned: booked, WhatsApp-lead booking, token over email, unmatched alert, low fit to nurture, no-show twice, handoff summary, duplicate, bad signature.

## Commits
None.

## Open items / deferred minors
Not started. K3 (does `metadata.lead` round-trip) is UNVERIFIED; fallback is attendee email.

## Review update (2026-11-13)
Use state-change reason `low_fit` for low-fit → NURTURE, and `sales_rejected` (CRM, later) for HANDED_OFF → NURTURE, so `holds.py` restarts colleagues correctly.
