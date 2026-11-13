# Task 12: Outreach starter + worker tick + mailbox health

## Status
⏳ To do. Not started.

## What it does (plain language)
Starts outreach on the chosen channel, switches channel once when needed, and runs the 60-second background loop for everything time-based: new leads, settled clicks, WhatsApp reminders (48 h), no-response switches, resuming out-of-office leads, end-of-sequence routing, Slack SLA alerts, attendance checks, score decay and a daily mailbox-health check.

## Files
Planned: `src/outreach/outreach.py`, `worker.py`, `health.py`, `tests/test_outreach.py`, `test_worker.py`, `test_health.py`. `tick` starts from the FastAPI lifespan when `RUN_WORKER=1`.

## Key functions and interfaces
Planned: `start_outreach`, `switch_channel`, `tick(session, deps, now)` with 11 jobs in a fixed order, `check_mailboxes` (bounce rate over 2% on 50+ sends in 7 days pauses the campaign).

## Depends on
Tasks 3 (`fallback_channel`), 4 (decay, settling), 6 (executor, ports, unposted alerts), 7 (`enroll`, `end_of_sequence_route`), 8 (WhatsApp sends), 9 (classifier wiring), 10 (SLA data), 11 (attended check).

## Used by
Task 13 (deploy runs the worker).

## Key decisions (from the rulings)
No rulings file yet. Carry-forward from Task 6: post `Alert` rows with `posted_at` NULL to Slack (retry) and send a digest of `integration_error` alerts. Pass an absolute path to `load_scanners`. This task provides the real `Deps` ports and the "rewiring" the ledger expects.

## Tests
Not started. Planned: both channels send only once, 47 h vs 48 h reminder, reply cancels reminder, OOO resume, SLA across weekend, idempotent tick, health thresholds.

## Commits
None.

## Open items / deferred minors
Not started. Instantly per-lead pause/resume stays manual until verified.
