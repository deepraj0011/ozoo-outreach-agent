# Task 09: Claude reply classifier

## Status
⏳ To do. Not started.

## What it does (plain language)
Reads an email reply or WhatsApp free text and labels it: interested, question, not_now, not_interested, unsubscribe, ooo, wrong_person or unsure. It then turns the label into actions. Any Claude failure or low confidence (below 0.7) becomes `unsure`, so a human sees it.

## Files
Planned: `src/outreach/classifier.py`, `tests/test_classifier.py`, and wiring of `Classify()` in `execute`.

## Key functions and interfaces
Planned: `ReplyClass(...)`, `classify_reply(text, channel, client)`, `actions_for_class(rc, lead)`. Uses a forced Claude tool call with a 20 s timeout. Reply text is wrapped in `<reply>` tags and treated as data. Quoted history is stripped.

## Depends on
Task 5 (actions), Task 6 (`Deps.classify` hook, `execute`), Task 3 (`intake_lead` for referrals).

## Used by
Task 12 (wires the real classifier into `Deps`), Task 10 (WhatsApp `question` uses `BotReply`).

## Key decisions (from the rulings)
No rulings file yet. Carry-forward from Task 6: a domain-matched reply (`data.matched_by == "domain"`) classified `unsubscribe` suppresses only `data.sender_email` and raises an alert, not the original lead. `interested` or `question` from a colleague alerts sales with the sender.

## Tests
Not started. Planned: one test per label, low confidence, API error, malformed output, prompt-injection safety.

## Commits
None.

## Open items / deferred minors
Not started. The carry-forward above is the main open item.

## Review update (2026-11-13)
State-change reasons matter now: `holds.py` reads them to decide when paused colleagues restart. Use `not_now` (and set `nurture_until` to the later date), `not_interested`, `wrong_person`.
