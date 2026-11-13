# Task 11: Cal.com booking + handoff

## Status
✅ Done: 2 fix rounds, reviewed, and the last round verified by the controller.
Commits: `7c4a09c`, `8236b23`, `b2ee102`. Tests: 825 → 982.

## What it does (plain language)
- **Lead token in the booking link.** Every booking link carries a signed lead token (`metadata[lead]`), so a booking is tied to the right lead. A forged token is ignored, and matching falls back to the attendee email.
- **Booking webhooks.** Cal.com calls `POST /webhooks/cal` with a signed body; the signature is `X-Cal-Signature-256`, bare hex or with a `sha256=` prefix. Each event is handled like this:
  - **Booked:** lead goes to BOOKED, and the meeting end and booking id are stored.
  - **Rescheduled:** the time is updated. If a lead in QUALIFYING (after a no-show) rebooks, it goes back to BOOKED.
  - **Cancelled:** lead goes back to QUALIFYING, and sales get an alert.
  - **No-show:** handled as before. If the lead was already handed off, sales get a "follow up" alert only.
  - **Meeting ended:** the lead goes to HANDED_OFF, and a summary is posted to Microsoft Teams.
  - **Routing form:** the lead gets a fit score of high, medium or low from company size and timeline.
    - **Low fit:** outreach stops, and the lead goes to NURTURE (reason `low_fit`) for 90 days.
    - **Matches no lead:** if the fit is high or medium, the form raises an alert.
- **Handoff summary.** The summary sales receive holds:
  - lead details;
  - channels;
  - score;
  - email steps sent;
  - the last 3 classified replies;
  - the last 20 chat lines;
  - form answers (capped);
  - booking times.

  The whole summary is capped at about 3500 characters.

## Files
- `src/outreach/booking.py`: signature check, parsing, `fit_from_answers`, handoff summary.
- `links.py`: `lead_token` and `parse_lead_token`.
- `rules.py`: the booking rules.
- `ingest.py`: the `unmatched_booking` and `unmatched_form` alerts.
- `app.py`: `/webhooks/cal` and the booking redirect.
- `config.py`: the fit thresholds.
- `tests/test_booking.py`.

## Depends on / used by
- **Depends on:**
  - Task 4: links.
  - Task 5: rules and the state machine.
  - Task 6: executor and alerts.
  - Task 10/10b: the Teams alerter.
- **Used by:**
  - Task 12: the worker's `booking_end` / no-show timing.
  - Task 13: the Cal.com setup.

## Key decisions
- The lead token has its own `L.` prefix, so it can never be swapped for a click token.
- Handoff happens as soon as the meeting ends, because sales hosted it.
- An event whose booking id is not the lead's current booking is ignored.
- Negated answers ("not now", "no rush") count as no timeline.
- "Under 50" counts as small.
- "10k+" means 10,000.
- If an event carries a timestamp that cannot be read, the webhook returns 400.

## Open items (go-live)
These must be confirmed on a real Cal.com account:
- whether `metadata[lead]` survives a routing-form URL;
- whether a reschedule carries the new booking id;
- `responseId`;
- whether answers come in `response` or `value`;
- which phone fields are sent;
- the signature format.

Also: set up the booking form with dropdown answers for company size and timeline.
