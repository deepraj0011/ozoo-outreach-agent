# Task 19: real leads, real tracking

**Status:** ✅ Done (2026-11-18)

## Why

Real leads don't read every email. Most never read any, and their inboxes are full of machines:
Apple Mail loads every image, Microsoft Defender opens every link, and bank gateways click links
minutes later while running JavaScript. Opens and clicks alone can't tell a person from a machine.
The agent had to (1) get a signal machines don't fake, (2) never act on a machine as if it were a
person, (3) send at times people actually read, and (4) be tested against leads that behave like
real ones.

## What changed

**1. Verified page visits** (`visits.py`, `links.py`, `app.py`). A tracked link to one of our own pages
forwards with `?oz_v=<signed token>`. The token holds lead, step, page and channel, and expires 2 days
after the click. The site's `/v.js` snippet counts active reading only and sends one beacon to
`POST /v/<token>` once the visit is engaged: 15+ s with 40 % scroll or 3+ interactions (mouse moves
don't count), or a second distinct page with 8+ s. The server re-checks the rule. The worker settles
the beacon and bot-filters it like a click. The rule `_on_visit` adds points once per page, sets
`verified_visit` and alerts sales `hot_lead` even while clicks need a check. Install guide:
`docs/guides/visit-tracking.md`. Off with `VISIT_TRACKING=false`.

**2. Clicks alone never email the lead** (`rules.py`, `worker.py`). While `CLICK_ALERTS_NEED_CHECK` is
on and nothing has confirmed a person (a reply, a booking or a verified visit):
- a pricing click sends no booking-slots email;
- the booking-click nudge is held (`booking_nudge_held`) and goes out on the first verified visit;
- a verified pricing visit sends the slots instead;
- at the end of the sequence, the `click_check` alert becomes `hot_no_reply` once a visit is verified.

**3. Send windows** (`sendwindow.py`, `emailtool/mailpit.py`, `mailpit_sender.py`). Our own sender
(mailbox / gmail) sends sequence emails Mon–Fri 09:30–17:30 in `EMAIL_SEND_TZ` (default
BUSINESS_TZ). Email 1 goes at the first slot. Follow-ups go at 15:00 / 10:30 / 15:30 plus a per-lead
offset of 0–40 min. Answers to a person go at once. The window is off locally unless set, and off
when `TIME_SCALE` ≠ 1.

**4. Realistic simulation** (`world/`). `python -m outreach.world [--leads N] [--seed S] [--days D]
[--out FILE]` runs persona leads through the real app, worker and sender. It uses a temp directory, a
simulated clock and a stand-in SMTP server, and prints truth vs agent per lead. There are 15 story
personas and a weighted mix for batches. Sample: `docs/samples/realistic-run.md`.

## Results

| Run | Outcome as expected | Hot alerts / wrong | Check alerts / on machines | Interested caught |
|---|---|---|---|---|
| Story (15 kinds) | 15 / 15 | 1 / 0 | 3 / 1 | 3 / 3 |
| Mix of 100 (seed 7) | 100 / 100 | 2 / 0 | 6 / 3 | 4 / 4 |

In the 100-lead mix, 537 of 542 link clicks were machines, and 215 of 272 opens were Apple,
Defender or sandboxes.

## Bugs found by the simulation and fixed

- End of sequence sent `click_check` for a lead already verified by a visit. It now sends `hot_no_reply`.
- A smart sandbox's pricing or booking click made the agent email a booking-slots message to a person
  who never read anything. That email is now held until a person is confirmed.

## Review fixes

An independent review of the change found these, all fixed:
- the booking nudge flag was spent while gated, so the nudge was lost (now held, then sent);
- a scanner's beacon could swallow the person's same-day beacon (dedupe now per network);
- visit tokens never expired (now 2 days);
- mouse jiggles counted as interactions, and reloads counted as pages;
- a failed `sendBeacon` was marked as sent;
- `--days` had no bound.

DST handling in `slot()` is left as is: Asia/Kolkata has no DST. A DST `EMAIL_SEND_TZ` needs a look.

## Tests

83 new tests:
- `test_visits.py`: 51
- `test_sendwindow.py`: 14
- `test_world.py`: 11
- `test_rules.py`, `test_worker.py`: 7 more

Total: 1737 passing.
