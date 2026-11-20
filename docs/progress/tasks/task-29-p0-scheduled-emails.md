# Task 29: P0 scheduled emails

**Date:** 2026-11-20 · **Status:** done · **Tests:** 1978 passing (after the final-review fix wave)

## Asked

The PM chooses which template goes as which email on which day; tracking is recorded only; unsubscribe,
bounce, reply and booking stop the rest.

## Built

- `src/outreach/p0/` (templates, schedule, planner, flow, console routes and JS), wiring in enrolment, worker jobs `p0_dispatch` / `p0_finish`, rules / actions / classifier branches, mailpit tool `steps`.
- Templates and Email schedule screens, lead Emails tab (pause, resume, send now, stop).
- `EMAIL_FLOW=p0` (default `classic`).
- `tests/test_p0_e2e.py`: full journey (Day 0 / 2 / 5, a click in between, one email per day, threaded `Re:` subjects) and unsubscribe after email 2.

## Final-review fix wave

- A reply pauses the sender at once (`ToolPause` without a date) before it is classified; out-of-office
  resumes it (unless the PM paused the lead); an unreadable reply (classify gave up) stops the emails
  (`reply_unread`) and keeps the alert. Any later reply from a P0 lead only alerts sales (never an email).
- Send now refuses with the reason (`P0Error`, 400) and changes nothing when the email cannot go now
  (paused, on hold, one already waiting in the sender, a reply being read): no double send.
- Day 0 = max(import start, now), one helper `p0.flow.start_p0` for enrolment and the "already in the tool"
  recovery.
- Tool variable `steps` is always `MAX_STEPS` (p0_finish ends the lead), so an email added later is not
  stuck; worker job `p0_stuck_handed` sends one `stuck_lead` alert per email still not sent 2 days after its slot.
- Schedule saves keep the accumulated shift (flag `p0_shift_s`) and cover every P0 lead with planned rows;
  an account-hold end shifts overdue emails like a resume.
- Lead controls refuse non-P0 / finished leads; `p0_finish` only for email leads; every P0 step uses the
  first-email send slot (`p0=1`); shown dates are the actual send slot; pause always pauses the sender;
  colleague-reply alert text; template id checks; Example dates keep unsaved edits; lead panel shows P0 sent / scheduled.
- Tests: `tests/test_p0_fixes.py`.

## Notes

Guide: `docs/guides/p0-email-schedule.md`. Rulings: `decisions.md` (Task 29).
