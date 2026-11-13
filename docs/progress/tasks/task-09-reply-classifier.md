# Task 09: Claude reply classifier

## Status
> Note: Slack was replaced by Microsoft Teams in Task 10b (2026-11-13). "Slack" below is historical; read it as Teams (`TeamsAlerter`, `/teams/commands`).

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


---
## Update — built and reviewed (2026-11-13)
**Status:** ✅ Done, review clean after 1 fix round. Commits `af4022a`, `721ea7b`. 70 tests (`tests/test_classifier.py`).

**What was built:** `src/outreach/classifier.py`.
- `classify_reply(text, channel, client, model, today)` asks Claude (forced tool call, 20 s timeout, 2 retries) to label a reply as one of 8 classes. It **never raises**: any error, timeout, truncated or malformed answer, empty text, or confidence below 0.7 becomes `unsure` → a human reviews it.
- Quoted email history is stripped before sending; the reply is wrapped in `<reply>` tags and treated as untrusted data.
- `actions_for_class()` turns the label into actions: interested → qualifying + booking link; question → Slack draft (email) or chatbot (WhatsApp); not_now → nurture until the date they gave (max 1 year); not_interested → closed; unsubscribe → suppressed on both channels; out-of-office → paused until the return date (max 60 days); wrong person → new referral lead; unsure → human.
- Colleague replies (matched by company domain) never trigger an automatic send.
- New actions: `SetNurtureUntil`, `IntakeReferral`.

**Security:** a referral contact is only added if it literally appears in the reply **and** is at the lead's own company domain (never gmail etc.). Otherwise a human decides. This stops a reply from making the agent email an outside address.

**Links:** uses Task 5 rules (a reply triggers `Classify`), Task 6 executor/ingest, Task 3 intake (referrals). Used by Task 10 (WhatsApp questions go to the chatbot; WhatsApp "unsure" starts a human takeover) and Task 12a (moves this Claude call off the webhook request).

**Open items:** Claude call currently runs inside the webhook request — fixed by new Task 12a.
