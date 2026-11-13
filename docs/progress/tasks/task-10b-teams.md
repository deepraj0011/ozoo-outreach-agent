# Task 10b: Microsoft Teams instead of Slack

## Status
✅ Done: 1 fix round, verified by the controller. Commits: `89df43c` and `98867c1`. Tests: 748 → 825.

## What it does (plain language)
The office uses Microsoft Teams, so every alert now posts as a card to the sales channel through a Teams Workflows webhook. Sales reply to leads by mentioning the agent in that channel:
- `@OzooAgent wa <lead_id> <message>`: sends as a human and mutes the bot.
- `@OzooAgent done <lead_id>`: gives the chat back to the bot and resets its turn count.
- `@OzooAgent help`.

## Files
- `src/outreach/alerts.py`: `TeamsAlerter`, `teams_escape`.
- `src/outreach/app.py`: `POST /teams/commands`.
- `src/outreach/config.py`: the Teams settings.
- `src/outreach/models.py`: `Lead.wa_human_owner_name`.
- `.env.example`.
- Tests: `tests/test_alerts.py`, `tests/test_handoff.py`, `tests/test_teams_only.py` (fails if "slack" appears anywhere in src/).

## Security
Each command is checked in this order:
1. The HMAC-SHA256 of the raw body, computed with the base64 security token, is verified before parsing.
2. The signed timestamp must be within ±5 minutes.
3. A replayed activity id is ignored.
4. Tenant, sales channel and sender must each be on an allow-list.

The command gets its answer straight away, and the WhatsApp send runs in the background. At send time the background job re-checks consent, opt-out and the 24 h window. A failure becomes an alert.

The Workflows URL is a secret and is never logged.

Lead and model text in cards is made inert: markdown characters are swapped for lookalikes, and links, `www.` and IP addresses are defanged after NFKC normalisation.

## Depends on / used by
- **Depends on:** Task 6 (alerts after commit), Task 8 (WhatsApp sender), Task 10 (takeover).
- **Used by:** Task 12 (retrying unposted alerts) and Task 13 (Teams setup in the runbook).

## Open items
- **Real tenant:** the real Teams payload shape and card rendering are unverified until tested on the tenant (see the go-live checklist).
- **Escaping:** the whole alert body is escaped, so our own wording also shows lookalike brackets.
