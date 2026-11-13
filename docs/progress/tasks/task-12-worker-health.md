# Task 12: Outreach starter, worker and mailbox health

## Status
✅ Done. Commits: `a99c7c0` and `fba97eb` (the review fix round). Tests: 1166 → 1391.

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

## Fixed in review round (`fba97eb`)
- **Stuck channel switch.** A lead switched from WhatsApp to email whose enrolment failed is now retried on the next tick: `start_new` checks the active channel.
- **Out-of-office resume.** The lead is re-enrolled in the email tool at the next unsent step. The campaign content is shifted (`step_offset`), and the old tool id is kept.
- **Refused WhatsApp reminder.** The lead switches channel straight away.
- **Worker lock.** The lock is renewed after every lead and every AI job.
- **Production delays.** Production refuses delay overrides shorter than the real defaults.
- **Smaller fixes.**
  - WhatsApp statuses are matched by message id.
  - A failing click is given up after 3 tries.
  - Shutdown stops cleanly between jobs.
  - A failed Postgres unlock invalidates the connection.
  - The alert reads "mailbox_unhealthy" when the mailbox was not paused.

## Open items
- After an out-of-office resume, the remaining emails restart the campaign spacing from the resume date.
- If stopping the tool lead after step 4 fails, the campaign could repeat step 4. This shows up as an integration error.
- Public holidays are not counted in the business-hours SLA.
