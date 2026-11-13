# Task 13: Run locally + local end-to-end test

## Status
✅ Done. Commits: `43716f9` (code) and `0ae48e1` (guide). Tests: 1245 → 1361. The controller smoke-tested it by hand. Deployment is deferred: the owner tests locally first.

## What it does (plain language)
- **Fake mode for every outside service.** Set per service with `EMAIL_MODE`, `WHATSAPP_MODE`, `LLM_MODE`, `ALERTS_MODE` and `VERIFY_MODE`. Nothing real is sent and nothing costs money.
  - The fake AI is a simple rule-based one.
  - Alerts print to the terminal.
  - You can switch one service to real at a time.
  - Fake mode is refused when `APP_ENV=production`.
- **One-command start.** Run `scripts/local_setup` once, then `scripts/run_local`.
- **Dev CLI.** `python -m outreach.dev` with:
  - `seed`: 5 sample leads.
  - `show <id>`: state, events, chat, jobs and alerts for one lead.
  - `tick`: run the worker once.
  - `advance --hours N`: moves a fake clock forward, so you can test the 48 h reminder now.
  - `sim <scenario>`: sends correctly signed webhooks for email events, clicks, WhatsApp buttons, texts and statuses, Cal.com events and Teams commands.
- **Local e2e test.** `tests/test_local_e2e.py` runs every row of the plan's end-to-end table in-process.

## Guide
[docs/LOCAL_TESTING.md](../../LOCAL_TESTING.md)

## Notes
- Run only one process that writes the database. The worker runs inside the app; do not also start `worker --loop`.
- Test phone numbers use `+1201555 01xx`, because the phone library rejects the `+1555…` range.
- The knowledge base `kb/ozoo_kb.md` is still a placeholder. Until marketing fills it, most bot questions go to a person.
- Email steps are announced with `sim email-sent --step N`. The real email tool keeps its own schedule.
