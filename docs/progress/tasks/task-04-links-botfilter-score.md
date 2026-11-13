# Task 04: Tracked links + bot filter + score

## Status
✅ Done. Reviewed clean after fix round 1.

## What it does (plain language)
Makes signed links so every click can be tied to a lead, step, purpose and channel. Decides whether an open or click came from a human, a security scanner (bot) or Apple Mail privacy proxy (machine). Turns human events into an engagement score with bands cold / warm / hot.

## Files
- `src/outreach/links.py`, `botfilter.py`, `score.py`, `config/scanners.yaml`
- `tests/test_links.py`, `test_botfilter.py`, `test_score.py`
- Fix round: `src/outreach/config.py`, `tests/test_config.py`

## Key functions and interfaces
- `make_link(lead_id, step, tag, channel="email", *, settings=None)`, `parse_token(token) -> (lead_id, step, tag, channel) | None`.
- `load_scanners(path)`, `classify_event(event, sent_at, sibling_clicks, scanners) -> "human"|"bot"|"machine"`.
- `apply_points`, `points_for` (added in Task 5), `decay`, `band`.
- Constants: `SCORE_POINTS`, `BOT_SEND_WINDOW` (60 s), `MULTI_TAG_WINDOW` (10 s), `DECAY_AFTER` (30 d).

## Depends on
Task 1 (settings), Task 2 (models).

## Used by
Task 5 (`points_for`, `band`), Task 6 (redirect, ingest verdict), Task 7 (links in emails), Task 8 (WhatsApp links), Task 12 (decay, click settling).

## Key decisions (from the rulings)
- Token = base64url payload plus 16 hex chars of HMAC; compared in constant time; any parse error returns None.
- Bot rules in order: WhatsApp click by user agent, scanner UA/IP, within 60 s of send, 2+ different tags in 10 s, Apple proxy open is `machine`.
- Fix round: `link_secret` needs 32+ chars, `intake_api_key` 24+, ten listed secrets must be non-blank. Error messages never echo secret values.

## Tests
52: links 12, botfilter 12, score 15, plus 13 added to `test_config.py` in the fix round.

## Commits
`77d728f` feat: signed tracked links, bot filter, engagement score. `bb08b6c` fix: validate secret strength in settings.

## Open items / deferred minors
- `apply_points` assumes `variant_flags` is a dict (None on an unflushed lead).
- A naive `sent_at` raises TypeError.
- `load_scanners` default path is cwd-relative; Task 12 should pass an absolute path.
- Missing tests: exactly-60 s boundary, scanner UA on a WhatsApp click.
- `link_secret` length is not stripped (whitespace-only passes).
- Blank `intake_api_key` error message says "length" instead of "blank".
