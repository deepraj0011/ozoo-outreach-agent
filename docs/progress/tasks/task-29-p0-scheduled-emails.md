# Task 29: P0 scheduled emails

**Date:** 2026-11-20 · **Status:** done · **Tests:** 1950 passing

## Asked

The PM chooses which template goes as which email on which day; tracking is recorded only; unsubscribe,
bounce, reply and booking stop the rest.

## Built

- `src/outreach/p0/` (templates, schedule, planner, flow, console routes and JS), wiring in enrolment, worker jobs `p0_dispatch` / `p0_finish`, rules / actions / classifier branches, mailpit tool `steps`.
- Templates and Email schedule screens, lead Emails tab (pause, resume, send now, stop).
- `EMAIL_FLOW=p0` (default `classic`).
- `tests/test_p0_e2e.py`: full journey (Day 0 / 2 / 5, a click in between, one email per day, threaded `Re:` subjects) and unsubscribe after email 2.

## Notes

Guide: `docs/guides/p0-email-schedule.md`. Rulings: `decisions.md` (Task 29).
