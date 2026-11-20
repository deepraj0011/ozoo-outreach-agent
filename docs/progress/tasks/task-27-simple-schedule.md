# Task 27: Simpler Email schedule screen

**Date:** 2026-11-20 · **Status:** done · **Tests:** 1880 passing

## Asked

"The current UI is too complex for the lead to understand and use. Even I am confused while using
it. ... simplify this with bestest UI." (The task-26 Plan & schedule screen: a branching tree of
emails with condition / link / wait cards, several named plans, and a lead tab with about 8 buttons.)

## Built

- **One schedule, a plain list** (`send_plan.py` rewritten). For each email there is a type and
  "N days after the previous email". There is one optional switch, "⚡ sooner if they show interest
  (click, page read or HOT): N days, as type X", and then "Stop N days after the last email".
  Named plans, defaults, delete, link-specific branches and per-branch dates are gone.
- Plans saved in the old tree format are converted when read (`upgrade`). The "otherwise" path gives
  the emails, and the first other branch gives "sooner if interested".
- Screen (`demo_schedule.js`):
  - a numbered vertical list, an Example box with the dates for a lead added today, an "Always" box, and Save / Reset;
  - the page is renamed **Email schedule**.
- Lead tab: sent emails ✓, one **Next** card with Send now · Change date · Pause/Resume · Stop
  emails, the emails still to come, and a folded History. Change kind was removed from the panel;
  the API still accepts it.
- Upcoming: rows open the lead on its Email schedule tab.
- Import: the plan selector was removed. Email 1 goes "as the schedule says" or "on a date".
- Routes: `/demo/api/plans` returns `{plan, following, options}`. `/plans/delete` was removed.

## Checked

- `tests/test_send_plan.py` was rewritten for the list model and old-plan conversion: 41 tests. Full suite: 1880 passed.
- I ran the app in an isolated temp dir (Mailpit, port 8011) whose DB held a task-26 tree plan:
  - the plan converted correctly;
  - the screen and lead tab rendered;
  - Pause, then Send now, sent email 3;
  - Upcoming listed email 4;
  - the browser console showed no errors.
