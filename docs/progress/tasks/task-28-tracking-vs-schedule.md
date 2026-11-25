# Task 28: Tracking vs. the schedule

**Date:** 2026-11-20 · **Status:** done · **Tests:** 1889 passing (+9)

## Asked

How do the PM's schedule and email tracking work together, for example a reply to email 1 or 3, or interest in
email 1? After the brainstorm, the user said "do them" for the five gaps found.

## Built

1. **Out-of-office:** when a lead comes back (worker `resume_paused`), `plan_back_at` is set. The next
   email then goes no sooner than `BACK_GAP_DAYS` (1) after that time.
2. **Re-check before sending:** `send_plan.recheck` handles an email that was decided but not sent yet.
   If interest arrives before it goes, it switches to the "sooner" type. It never overrides a PM change
   or a paused email. The plan job now also looks at these queued leads.
3. **"Sooner" can end the sequence:** `fast.last` adds the option "and make it the last email". It sets
   `plan_last_step`, so the sequence then ends `end_days` later.
4. **"Not now" check-in:** the new worker job `nurture_follow_up` sends one `nurture_checkin` reply in the
   same thread once `nurture_until` passes. It is guarded by `nurture_checkin_at`, so a lead never gets
   it twice. The new optional template is in `templates/emails.yaml`.
5. **Lead panel:** a sent email shows "⚡ sent sooner"; "👀 showed interest since email N" shows when
   there was interest; the Next card shows "The last email" where it applies. The new flag `plan_fast`
   records which emails went sooner.

## Checked

- 9 new tests in `tests/test_send_plan.py`:
  - the out-of-office gap, including that an old one has no effect;
  - re-checking a queued email, and that a PM change is respected;
  - `last` with and without interest;
  - the interest flag in the view;
  - a worker run: resume sets the back time;
  - a worker run: one check-in on the date and none twice.
- Full suite: 1889 passed.
- Smoke test in an isolated env:
  - the schedule with `last` saved;
  - email 1 was sent;
  - the screen JS parses.

## Incident during the smoke test

The dev CLI ignored `OUTREACH_ENV_FILE`. Its `sim` command defaults to the user's server on port 8000.
As a result:

- `dev seed` added 5 sample leads (ids 6–10, source `local_seed`) to the user's `local.db`;
- two simulated case-study clicks were recorded on the user's lead 1 (score 0 → 10).

The emails go only to the local Mailpit. Cleanup is up to the user. CLAUDE.md now documents
`--env-file` / `--base-url`.
