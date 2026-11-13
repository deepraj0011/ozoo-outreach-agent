# Task 13: Deploy + end-to-end test

## Status
⏳ To do. Not started.

## What it does (plain language)
Packages the app (Dockerfile), deploys it, configures the external tools (email tool, Meta, Cal.com, Microsoft Teams), documents it in a runbook and runs an end-to-end checklist. A simulated e2e test with fakes is built locally. The real-inbox run needs real accounts and DNS, so the user does it.

## Files
Planned: `Dockerfile`, `docs/runbook.md`, `scripts/e2e_checklist.md`, `tests/test_e2e_sim.py`.

## Key functions and interfaces
Runs `uvicorn outreach.app:app` with `init_db()` at start. Checklist has about 17 rows across email-only, WhatsApp-only and both-channel leads, plus a duplicate-webhook check.

## Depends on
Everything: tasks 6 (app), 8, 10, 11 (webhook and slash routes), 12 (worker).

## Used by
Go-live. See [go-live-checklist.md](../go-live-checklist.md).

## Key decisions (from the rulings)
Task 13 delivers Dockerfile, runbook, checklist and the simulated test; the real-inbox run is the user's.

## Tests
Not started. Planned: `tests/test_e2e_sim.py` driving every checklist row with fakes.

## Commits
None.

## Open items / deferred minors
Not started. Runbook must cover these carry-forwards:
- Disable or redact uvicorn access logs (tokens in `/r`, `/u`) and note X-Forwarded-For trust.
- App logging config must keep httpx at WARNING or higher.
- Python 3.12 in Docker vs 3.10 locally.
