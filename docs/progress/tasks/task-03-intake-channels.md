# Task 03: Intake gate + channel choice

## Status
✅ Done. Reviewed clean after fix round 1.

## What it does (plain language)
Decides whether a new lead may enter and which channel it starts on. It validates the fields, checks the suppression list, removes duplicates, verifies the email with ZeroBounce and normalises the phone number. It never raises: bad leads come back as a "rejected" result.

## Files
- `src/outreach/verify.py`, `channels.py`, `intake.py`
- `tests/test_intake.py`, `tests/test_channels.py`, `tests/test_verify.py`

## Key functions and interfaces
- `intake_lead(session, payload, verifier, now, primary_when_both="whatsapp") -> IntakeResult` (flushes, no commit).
- `IntakePayload` (pydantic), `IntakeResult(lead_id, outcome, ...)`.
- `EmailVerifier(api_key, client=None)` calls ZeroBounce with a 10 s timeout.
- `normalize_phone`, `email_usable`, `whatsapp_usable`, `choose_channel`, `fallback_channel`.

## Depends on
Task 1 (settings), Task 2 (Lead, states, Suppression).

## Used by
Task 6 (`POST /intake`), Task 9 (`wrong_person` referral creates a lead), Task 12 (`switch_channel` uses `fallback_channel`), Task 5 (snapshots reuse channel logic).

## Key decisions (from the rulings)
- Unique-constraint race is a real bug, not a nit: the insert runs in a savepoint and maps to `skipped_duplicate`.
- Suppression is checked before dedup. Account hold: a new lead whose colleague is already engaged is created as `PAUSED`.
- Email usable needs consent and verify status in valid / catch_all / unknown.

## Tests
31: intake 20, channels 10, verify 1.

## Commits
`20c111e` feat: intake gate with channel policy, phone normalisation, dedup, suppression. `d093b54` fix: no PII in httpx logs; intake unique-race maps to duplicate.

## Open items / deferred minors
- `_EMAIL_RE` is permissive.
- `fallback_channel` case (email active, no opt-in) is untested.
- `IntakeResult` is a plain class.
- Any `IntegrityError` maps to `skipped_duplicate`; narrow it.
- httpx log silencing is set at import time. Task 13 must keep httpx logging at WARNING or higher.
- SQLite SAVEPOINT workaround not applied here (production is Postgres).

## Review update (2026-11-13)
- Email no longer needs `consent_email` (recorded only); every email is verified. Leads are rejected only with no valid email and no WhatsApp opt-in.
- EU / EEA leads: `is_eu` + `email_hold="eu_approval"` unless `EU_EMAIL_APPROVED`. Email-only EU leads → `PAUSED`, outcome `held_eu`.
- Account hold now also triggers on a HANDED_OFF colleague, and stores `paused_by` (the blocking colleague's id).
- Tests: `tests/test_eu.py` (new), extra cases in `test_intake.py`, `test_channels.py`.
