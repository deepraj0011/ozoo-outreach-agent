# Task 10: WhatsApp chatbot + human takeover

## Status
⏳ To do. Not started. This is the planned cut line: if time runs short, build only the Slack takeover part.

## What it does (plain language)
Answers WhatsApp questions from a knowledge base with Claude, within strict guardrails (no prices, no promises, max 6 bot turns). When unsure or asked for a human, it alerts sales in Slack with the transcript. Sales replies with `/wa <lead_id> <message>` and releases with `/wa-done`.

## Files
Planned: `src/outreach/chatbot.py`, `handoff.py`, `kb/ozoo_kb.md`, route `POST /slack/commands`, `tests/test_chatbot.py`, `tests/test_handoff.py`.

## Key functions and interfaces
Planned: `BotResult`, `bot_reply(lead, history, kb, client)`, `start_takeover(session, lead, reason, alerter, now)`, Slack signature check (v0, 5-minute limit). `execute(BotReply())` routes `answer`, `ready_to_book`, `wants_human`, `out_of_scope`, `not_ready`.

## Depends on
Task 5 (`BotReply`), Task 6 (`Deps.bot_reply`, `handoff`, alerter), Task 8 (WhatsApp send, window), Task 9 (same Claude plumbing).

## Used by
Task 12 (SLA check `wa_sla` reads `takeover_at`), Task 13 (Slack slash commands setup).

## Key decisions (from the rulings)
No rulings file yet. From the plan: price answers not in the KB become a handoff; any Claude error hands off to a human.

## Tests
Not started. Planned: answer, no price quotes, 6th-turn offer, API error, wants human, `/wa` send and mute, outside-window error text, `/wa-done`, bad signature.

## Commits
None.

## Open items / deferred minors
Not started. Marketing must fill `kb/ozoo_kb.md` before go-live.
