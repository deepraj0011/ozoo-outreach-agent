# Task 14: Local email mode with Mailpit (EMAIL_MODE=mailpit)

## Status
✅ Done. 24 tests in `tests/test_mailpit.py`. Live smoke test against a real Mailpit (separate env file, DB and port, so the user's `.env.local` / `local.db` were never touched):
- seed → Email 1 for both email leads in Mailpit within seconds, and `step_sent=1` on the lead;
- a real click on the email's booking link → score 10, plus a "pick a time" reply in the same thread;
- `sim email-reply "Interested"` → `qualifying`, plus the booking-link reply in Mailpit;
- `advance --hours 96` → Email 2 for the lead still in sequence, none for the replied lead;
- no email addresses in the server log.

## What it does (plain language)
A free, local-only way to see the real emails. Instead of Instantly, the agent sends each email over SMTP to **Mailpit**, a fake mail server on this machine with a browser inbox at http://localhost:8025. Nothing reaches the internet.

You see Email 1 to 4 exactly as a lead would, and the booking, reschedule and slot replies too. Clicking a link in Mailpit goes through our tracked redirect, like a real click. Replies from the lead are still simulated with `python -m outreach.dev sim email-reply`.

## Files
- `src/outreach/emailtool/mailpit.py`: `MailpitEmailTool`, which implements the `EmailTool` port, including `block` and `find_lead`.
- `src/outreach/mailpit_sender.py`: `python -m outreach.mailpit_sender [--once]`.
- Wiring: `config.py` (`EmailMode`, `smtp_host` loopback check, `smtp_port`, `mailpit_state_file`, mailpit refused in production, not counted as real sending), `app.py` `default_deps`, `scripts/run_local` (starts the sender, keeps `--no-access-log`), `.env.example`, `docs/LOCAL_TESTING.md` section 4b.
- `tests/test_mailpit.py`: 24 tests. SMTP is a recording stub, so no network is used.

## How it works
- **Shared state.** The state lives in a small SQLite file, `.mailpit_tool.db` (git-ignored), shared by the server and the sender process. It records each enrolled lead's variables, enrolment time, last step sent, last step reported, and status: active, paused, stopped or done. Blocked addresses are kept in a separate table.
- **Enrolment.** `add_lead` only records the lead. The sender sends Email 1 on its next pass, a few seconds later.
- **Schedule.** Steps follow `SCHEDULE_DAYS` (days 0, 4, 9, 15) from enrolment, on the local fake clock, divided by `TIME_SCALE`. One step goes out per lead per pass.
- **Stop, pause and block win.** They write to the state file at once. Before each SMTP send, the sender re-reads the lead inside the same write transaction, so a stop that lands after the due list was built still prevents the send.
- **Reporting back.** After sending, the sender POSTs the same signed `email_sent` webhook Instantly would send (built with `devsim.email_event`) to the running app. If the app is down or answers with an error, the report is retried on the next pass (`notified_step`).
- **One-off replies.** `reply()` sends at once, threaded with `In-Reply-To` on the last message.

## Safety
- The SMTP host must be loopback (`127.0.0.1`, `localhost` or `::1`). The tool refuses any other host, so this mode can never relay to a real mail server.
- Settings refuse `EMAIL_MODE=mailpit` outside `APP_ENV=local` and any non-loopback `SMTP_HOST`.
- Logs carry tool and lead ids only, never addresses, subjects or bodies (tested).

## Depends on
Task 6 (EmailTool port, Instantly webhook parser), Task 7 (`SCHEDULE_DAYS`, `subject_N` / `body_N` variables), Task 13 (`LocalClock`, `devsim`, `dev._local_settings`).

## Wiring (done)
1. `config.py`:
   - `email_mode` accepts `mailpit` (local only).
   - Add `smtp_host` (loopback validated, default `127.0.0.1`), `smtp_port` (1025) and an optional `mailpit_state_file`.
2. `app.py` `default_deps`: when `email_mode == "mailpit"`, build `mailpit_sender.make_tool(settings)`.
3. `scripts/run_local`: when `EMAIL_MODE=mailpit`, start `python -m outreach.mailpit_sender` in the background.
4. `docs/LOCAL_TESTING.md`: a "See real emails with Mailpit" section. Also a `.env.example` comment.

## Open items
- A click within 60 s of a send is marked `bot` (bot filter rule), so wait a minute after an email arrives before clicking its links.
- Bounces and unsubscribes reported by the tool are not simulated in Mailpit. Use `sim email-bounce` / `sim email-unsub`, or the email's own unsubscribe link.
