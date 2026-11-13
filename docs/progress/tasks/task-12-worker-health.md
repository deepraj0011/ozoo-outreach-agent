# Task 12: Outreach starter, worker and mailbox health

## Status
✅ Built in commit `a99c7c0`. Tests: 1166 → 1245. Review done: no critical problems. A fix round is queued (see Open items).

## What it does (plain language)
- **Starting outreach (`outreach.py`).** The agent starts each new lead on its channel. A lead with both email and WhatsApp is contacted on exactly one of them. The WhatsApp intro is never sent twice: the "attempted" flag is saved before the send. If WhatsApp refuses the number, the lead falls back to email through the rules.
- **Switching channel.** A lead switches channel once; after that it goes to nurture.
- **The worker (`worker.py`).** It runs every 60 s inside the app when `RUN_WORKER=1`. You can also run it by hand with `python -m outreach.worker --once` or `--loop`. Its jobs, in order:
  1. Start new leads.
  2. Settle clicks after 30 s and apply their points.
  3. Booking-click nudge after 30 min.
  4. WhatsApp reminder at 48 h.
  5. Switch channel 48 h after the reminder if there is no reply.
  6. Reconcile WhatsApp sends whose outcome was unclear.
  7. Release out-of-office holds.
  8. Release account holds.
  9. Release EU holds, only when they have been approved.
  10. End the email sequence 5 days after email 4.
  11. Takeover SLA alert after 1 business hour (Mon–Fri 09:00–18:00, `BUSINESS_TZ`), with a re-alert when the lead keeps writing.
  12. Hand off if the meeting ended more than 2 h ago and the webhook was missed.
  13. Score decay.
  14. Run pending AI jobs (Task 12a).
  15. Retry Teams alerts that failed to post, and send an hourly integration-error digest.
  16. Daily mailbox health check.

  Each job runs on its own, so one failing job never stops the others. Only one worker runs at a time (a lock). Running the worker twice in a row causes no duplicate sends.
- **Mailbox health (`health.py`).** Checks the bounce rate per campaign over 7 days. Above 2% with at least 50 sends, it raises an alert for a manual pause, because Instantly has no pause through its API.

## Files
- New: `src/outreach/outreach.py`, `worker.py`, `health.py`, `hours.py`.
- Changed: `app.py` (`wire_deps`, lifespan loop), `config.py`, `models.py` (`worker_lock`, `worker_mark`), `alerts.py` (repost, digest), `rules.py` (`booking_click_not_booked`), `botfilter.py` (absolute path).

## Depends on / used by
Depends on nearly everything: rules, executor, sequencer, holds, WhatsApp sender, jobs (12a), alerts, booking. Used by Task 13 (the local kit drives it with `dev tick` and the fake clock).

## Open items (fix round queued)
- If a WhatsApp→email switch fails to enrol the lead, it is never retried. The fix filters `start_new` by the active channel.
- A lead returning from out-of-office must be re-enrolled in the email tool at the next unsent step.
- If the WhatsApp reminder is refused, switch channel at once.
- Renew the worker lock during long AI-job batches.
- Production must refuse delay overrides shorter than the real defaults.
- Smaller items:
  - match WhatsApp delivery statuses by message id;
  - cap retries of a failing click;
  - clean stop between jobs on shutdown;
  - Postgres unlock failure;
  - alert wording when a mailbox was not paused.
- Public holidays are not counted in the business-hours SLA.
