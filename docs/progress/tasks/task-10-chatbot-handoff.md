# Task 10: WhatsApp chatbot + human takeover

## Status
✅ Done after 2 fix rounds; the second review was clean. Commits: `f792478`, `e76a81b`, `739846b`. Tests: 641 → 748.
**Being replaced:** in Task 10b, Slack becomes Microsoft Teams (the office uses Teams). The chatbot logic stays as it is. Only the alert channel and the sales commands change.

## What it does (plain language)
- **Answers questions.** When a lead asks a question on WhatsApp, Claude answers from `kb/ozoo_kb.md`.
- **Guardrails.** Every answer is checked before it is sent:
  - **Prices:** any price in the answer must appear in the KB with the same currency and amount. Forms like "$49", "500 per month", "99/mo" and "300 dollars" are all caught.
  - **Links:** no links.
  - **Length:** at most 1000 characters.
  - **Turn limit:** at most 6 bot turns. After that, the lead gets the `turn_limit` text, which offers HUMAN.
- **Hands off to a person.** Any failure, unsure answer, "wants human" request or exact "human" message hands off. The lead gets a short notice if the 24 h window is open. Sales gets an alert with the last 10 messages, each cut to 300 characters.
- **Sales commands.** `/wa <lead_id> <message>` sends as a person and mutes the bot. `/wa-done <lead_id>` gives the chat back to the bot and resets its turn count.
- **Escaping.** Lead text is never trusted in alerts. All alert text is escaped in one place, at the moment it is posted.

## Files
`src/outreach/chatbot.py`, `src/outreach/handoff.py`, `src/outreach/alerts.py` (escaping), and the `POST /slack/commands` route in `app.py`. Also `templates/whatsapp.yaml` (`turn_limit`, `handoff_notice`), `kb/ozoo_kb.md`, `tests/test_chatbot.py` and `tests/test_handoff.py`.

## Key functions and interfaces
- `bot_reply(...) -> BotResult`. It never raises; any failure becomes `wants_human`.
- `post_check`, `prices(text)` and `kb_prices(kb)`, which work on (currency, amount) pairs.
- `start_takeover(session, lead, reason, ...)`.
- Executor action `WaSendRaw` and the `StartTakeover` handling. The classifier's WhatsApp "unsure" now starts a takeover.

## Depends on
- Task 5: `BotReply` rules; the bot is muted while `wa_human_owner` is set.
- Task 6: alerter after commit.
- Task 8: WhatsApp sender, which checks consent, suppression and the 24 h window before every send.
- Task 9: Claude plumbing.

## Used by
- Task 10b: Teams replaces Slack.
- Task 12: takeover SLA and re-alerting.
- Task 13: setup runbook.

## Key decisions
- **Prompt-injection safety.** Chat history is treated as untrusted data. Turns are capped at 4096 characters and only the last 20 are kept. The model's output is validated strictly.
- **Price matching.** Prices in the KB are matched as normalised (currency, amount) pairs, not substrings. "$4" no longer passes against "$49", and "$49" no longer passes against "€49".
- **Escaping.** Every alert is escaped centrally when it is posted. `Alert.text` stores the raw text, so a retry re-post escapes it only once.
- **Sales handoff.** An exact "human" message hands off without an LLM call. `/wa-done` resets the bot's turn count.

## Tests
Cover:
- every price form, KB price matching and currency mismatch;
- false positives that must still pass ("24/7", "3 users", dates);
- the bot muted during takeover;
- an alert with `<!channel>` escaped;
- Slack signature, timestamp and allow-list;
- no message text or phone numbers in logs.

## Open items / deferred minors
- **KB price format.** Write KB prices in "1,000.00" format. European "1.000,00" is misread. Goes in the runbook.
- **Takeover follow-up.** No re-alert when the lead keeps writing during a takeover, and a takeover has no timeout. Carried to Task 12.
- **KB reload.** The KB is loaded at startup, so edits need a restart.
- **Marketing.** Marketing must fill `kb/ozoo_kb.md` before go-live.
