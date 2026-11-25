# Task 32: P0 schedule views (15 emails, day ruler, list editor, dot-track board)

**Date:** 2026-11-25 · **Status:** done · **Tests:** 2047 passing (`.venv/bin/pytest -q`), 51 vitest (`cd frontend && npm test`)

Branch `p0-schedule-views` (local, from `main`).

## Asked

Finish the P0 screens from the approved mockups: sections A (day ruler), C (edit as a list), D (board with
dot tracks); raise the limit to 15 emails; make weekend dates shown match the real send day. B (calendar)
and E (per-email progress) are out of scope.

## Built

- **15 emails**: `p0/schedule.py` `MAX_EMAILS = 15`, sender `emailtool/mailpit.py` `MAX_STEPS = 15` (the
  P0 `steps` variable). Frontend `MAX_EMAILS` in `frontend/src/lib/schedule.ts`.
- **Weekend dates**: `planner.row_goes` / `planner.is_late`. A handed email was shown (board, lead panel)
  at its hand-off moment, e.g. Saturday 00:05, and went "late" on Sunday, although the sender waits for
  Monday's slot. Now a handed row is dated by the send-window slot of its hand-off (as the sender and the
  stuck-handed check already do) and is late only a day after that slot. Planned rows and example dates
  already used the slot. Fast clock (`TIME_SCALE`): sender and screen both skip the window, unchanged.
- **A · Day ruler** (`components/ScheduleRuler.tsx`, replaces `ScheduleStrip`): emails on a day axis,
  Now / Day / fixed-date markers, alternating labels (when · template · date a lead added today gets it), week
  bands, legend, "N emails" and "X days, ends <date>" chips, Edit. Layout is the pure `rulerLayout` (min gap
  between neighbours so same-side labels never touch). Fits 15 emails at 1280 px; scrolls inside its card
  when narrower.
- **C · List editor** (`components/ScheduleEditor.tsx`, a wide dialog, full screen on small screens;
  replaces `ScheduleDrawer`): per row number badge, template, Now|Day|Date toggle (Now only for email 1), day
  or datetime, gap bar "+N days", real date from `/demo/api/p0/schedule/example` with a weekend or "Same
  moment for every lead" note; remove; "Add email N" up to 15; Cancel / Save with API errors inline. Same
  save semantics as before.
- **D · Board** (`components/BoardTable.tsx`; `EmailCell` removed): Lead | track header (numbered markers,
  D0 / D4 / 14/12) | Status + "k of N sent" | Next. Dots are buttons (aria-label and tooltip "Email N: …")
  opening the email drawer; the lead name opens the lead panel. Pure helpers in `lib/campaign.ts`:
  `nextLabel`, `dotLook`, `dotHint`, `trackLabel`, `sentCount`.
- Tests: `tests/test_p0_schedule_views.py` (15 saves and plans 15 rows, 16 refused, all 15 handed and sent by
  the sender, handed weekend email shown on Monday and not late on Sunday, lead panel dates a handed email by
  its hand-off); vitest `lib/schedule.test.ts`, new cases in `lib/campaign.test.ts`.

## Click test

Isolated temp dir (own env file and SQLite DB, `EMAIL_MODE=mailpit` to a throwaway SMTP sink on 1035,
`EMAIL_FLOW=p0`, worker in the app, port 8011), 8 leads + 3 from the sample CSV. Clicked: Start; schedule
Edit, change a day, add to 12, switch one to Date and time, Save; later to 15 (Add disabled at 15) and an
invalid day (inline error); a dot (email drawer); a lead: Pause, Resume, Send next now, Stop emails
(confirm); Templates: Switch off refused for a scheduled template, new template Save draft, Save and make
live, Switch off; Leads: CSV import (3 added). No console errors.

Fixed during the click test: the ruler scrolled at 12 emails on 1280 px (tighter spacing), week captions
overlapped labels (moved to a bottom strip), long Next text clipped (wraps to two lines), editor fields had
no id/name.

## Open

- The browser still reports "Incorrect use of `<label for>`" for MUI select fields (an MUI quirk).
- Locally (`EMAIL_SEND_WINDOW` unset) emails do go on weekends, so the weekend note only shows with a send
  window set.
- The design docs (`docs/plan/p0-scheduled-emails-*.md`) still say 1 to 10 emails (historic).
