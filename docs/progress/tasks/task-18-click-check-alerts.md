# Task 18: clicks alone don't alert sales (trial safety for real leads)

**Status:** ✅ Done (2026-11-18)

## Why

The bot filter catches Microsoft Defender and Apple, but a bank's security sandbox (Proofpoint,
Mimecast and others) can visit links minutes after delivery and run JavaScript, so it can pass as a
person. One *pricing* click alone used to send a `hot_lead` alert to sales. For the first real leads,
a click is a hint, not proof.

## What changed

- New setting `CLICK_ALERTS_NEED_CHECK` (default **true**).
- While a lead is still in outreach (`in_sequence`, `paused`, `nurture`, so no reply and no booking
  yet), a human click that would have sent `hot_lead` now sends one **`click_check`** alert. Its text
  reads "link clicks only, no reply or booking yet: may be a company security scanner. Check before
  contacting". The flag `click_check_alerted` stops repeats.
- `hot_alerted` is **not** set by that alert, so a later real signal still reaches sales as usual.
- End of sequence with a hot score (which can only come from clicks, because there was no reply)
  sends `click_check` instead of `hot_no_reply`. The lead still goes to the `sales_personal` track.
- Unchanged: points are still added, and the booking-slots email after a pricing click is still sent
  (it's harmless if the click was a bot). Clicks after a reply (`engaged`, `qualifying`, `booked`)
  alert `hot_lead` as before.
- Rules stay pure: the setting reaches `rules.decide` as `LeadSnapshot.clicks_need_check`, which the
  worker fills from settings when it settles clicks.

Set `CLICK_ALERTS_NEED_CHECK=false` once a few weeks of real data show the click filter holds.

## Files

`config.py`, `rules.py` (`_score`), `sequencer.py` (`end_of_sequence_route`), `worker.py`,
`actions.py` (`click_check` alert kind), `.env.example`, `docs/onboarding/03-tracking-explained.md`.

## Tests

`test_rules.py` +5, `test_worker.py` +1 (check off keeps `hot_no_reply`), `test_local_e2e.py` updated
(pricing click gives `click_check`; a later reply still moves the lead). 1654 passing.
