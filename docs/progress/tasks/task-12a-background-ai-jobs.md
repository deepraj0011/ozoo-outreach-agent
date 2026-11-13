# Task 12a: AI calls as background jobs

## Status
✅ Done after 1 fix round. Commits: `b354fc1` and `b87c6bc`. Tests: 1126 → 1166.

## What it does (plain language)
Webhooks (email reply, WhatsApp message, Cal.com) now answer straight away. The AI work happens afterwards:
- A reply to classify, or a WhatsApp question to answer, is saved as a **job** in the `job` table.
- The job runs in the background right after the response is sent.
- The worker (`run_due`) picks up any job still pending after 2 minutes. This covers a crash or restart.

## Safety
- **Exactly once.**
  - A job is claimed with a conditional database update.
  - Every write that finishes a job checks that the run still owns it.
  - Before acting on the AI's answer, the run locks the job row and confirms it still owns the job.
  - A slow run that was taken over by a newer run throws its result away.
- **Retries.**
  - A failed AI call is retried after 1 minute, then after 5 minutes.
  - After 3 failures:
    - for an email reply, sales get an "unsure, please read it" alert;
    - for a WhatsApp chat, it is handed to sales.
  - In both cases an integration error is also recorded.
- **Message bursts.** If a lead sends several WhatsApp messages quickly, the bot answers once, using the newest job.
- **Privacy.** Before any AI call, a chatbot job is skipped in these cases:
  - the lead has no WhatsApp consent;
  - the lead's number is blocked;
  - the 24-hour window is closed;
  - the lead is closed;
  - a person has taken over.

  The lead's text is not sent to the AI provider for nothing.
- **Error safety.** The job runner never crashes the request or the worker. Error records keep only the error type, never message text.

## Files
- `src/outreach/jobs.py` (new)
- `models.py`: `Job`
- `actions.py`: `Deps.jobs` and enqueue
- `ingest.py`: returns the ids of the jobs it enqueued
- `app.py`: BackgroundTasks on the email, WhatsApp and Cal.com webhooks
- `tests/test_jobs.py`

## Depends on / used by
- **Depends on:** Task 6 (executor), Task 9 and 9b (classifier and AI port), Task 10 (chatbot and takeover).
- **Used by:** Task 12, whose worker tick calls `run_due`.

## Open items
- **SQLite gap.** On SQLite, the row lock does nothing. A theoretical double-send gap remains if two workers run at once. Task 12's worker lock keeps it to one worker. Postgres is fully covered.
- **Long transaction.** The AI call runs inside the job's database transaction. This is fine for now; revisit it if Postgres runs short of connections.
