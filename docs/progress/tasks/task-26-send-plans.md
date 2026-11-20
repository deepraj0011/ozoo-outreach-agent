# Task 26: Plan & schedule (PM-controlled email timing with branches)

**Date:** 2026-11-20 · **Status:** done · **Tests:** 1878 passing (+39)

## Asked

The team lead: there is no way to schedule emails; the dates are fixed. The PM should set when
email 1 goes, and after each email decide from the tracking which email goes next and on which day.
"Scheduling should be under PM control, what will be sent will be AI generated." Flexible, no passcode,
a clear UI that shows the flow.

## Built

- `send_plan.py`: plans (a tree of up to 4 emails; a start rule; for each email a kind and branches HOT /
  read a page / clicked (any or one link) / otherwise, each with "after N days" or a date and time,
  leading to the next email or the end), validation with plain messages, the decision
  (`next_email`, `commit_next`), per-lead changes (`change_next`: send now, move, kind, hold,
  resume, undo, stop, move onto a plan), the lead view and the upcoming list.
- `SendPlan` table; lead flags `plan_id`, `plan_path`, `plan_kinds`, `plan_next`, `plan_next_sched`,
  `plan_log`, `plan_ended`, `plan_start`.
- Email tool (mailpit / gmail / mailbox): `SUPPORTS_SCHEDULE`. A lead enrolled with `plan=1` sends a step
  only once it has `due_at_<step>` (moved into the send window unless `due_exact_<step>`; `hold` means wait).
- Sequencer: `enroll` attaches the plan (the lead's own, else the default) when the tool supports it;
  `choose_version` / `render_step` take the plan's kind; `refresh_next_step` leaves plan leads alone.
- Worker: job `plan_schedule` after `start_new`; `end_of_sequence` now runs only for fixed-schedule leads.
  The stuck-lead check counts from a plan's later email-1 date and skips held leads.
- Intake: optional `plan_id` and `plan_start`; the import dialog sets them.
- Web app: **Plan & schedule** page (flow builder, a preview for each email, save / default / delete,
  Upcoming emails by day), the lead panel's **Email schedule** tab, and the import options.
- Guide: `docs/guides/send-plans.md`.

## Checked

- 39 new tests (`tests/test_send_plan.py`):
  - validation, defaults and the email-1 start modes;
  - branch choice from human clicks, visits and HOT, counted only since the last email;
  - link-specific branches, and the send window versus an exact time;
  - ending early and after email 4;
  - every PM change, including on an email already decided;
  - plan edits that remove an email;
  - the routes, the import fields, and a worker run from email 1 to nurture.
- Ran the app in an isolated temp dir (Mailpit, port 8011):
  - saved the starter plan and added a lead; email 1 was sent;
  - the Email schedule tab showed both branches;
  - after changing the kind and pressing Send now, email 2 went out as a case study;
  - the Upcoming tab listed email 3;
  - no browser errors.

## Not done / limits

- Instantly (`EMAIL_MODE=real`) keeps its own campaign schedule; plans need our own sender.
- At most 4 emails per lead (the rest of the system is built on 4 steps). Emails after the sequence
  (nurture) are still not sent; that is a separate task.
- The content comes from the existing role-based template engine (per role and company), not free LLM text.
