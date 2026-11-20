# P0 scheduled emails: templates and schedule

With `EMAIL_FLOW=p0` the product manager decides exactly which template goes as which email on which
day. Tracking (clicks, page visits) is recorded and shown, never acted on. Design: `docs/plan/p0-scheduled-emails-design.md`.

## Templates screen

- A template has a name, a subject and a body. Status: **draft** (editable, not used), **live** (can be put
  on the schedule), **off**. **Preview** shows it for a sample lead.
- Placeholders: `{first_name}`, `{name}`, `{role}`, `{company}`, `{signature}`, `{booking_link}`, `{homepage_link}`,
  `{product_link}`, `{voice_link}`, `{chat_link}`, `{pricing_link}`, `{case_study_link}`, `{trial_link}`,
  `{unsubscribe_link}`. An unknown placeholder is rejected on save. If the body has no
  `{unsubscribe_link}` one is appended.
- A template that is still on the schedule or on a lead's unsent email cannot be switched off.

## Email schedule screen

A list of 1 to 10 emails, each with a live template and a **Day** (0 to 365, strictly increasing).
Day 0 is the day the lead is added; Day N is calendar day N in `BUSINESS_TZ` (Asia/Kolkata). The Example column shows the dates for a lead added now.

- At most one P0 email per lead per calendar day, in the next working-hours slot.
- Email 1 has the template's subject. Later emails go in the same thread as `Re: <email 1 subject>`.
- 5 days after the last email the lead moves to nurture (no alert).
- Editing the schedule changes the lead's emails not yet handed to the sender; one already handed over is not rewritten.

## Lead panel, Emails tab

Each planned email shows planned / handed / sent / cancelled / skipped. A **late** tag means the email went
later than planned (for example a weekend or a pause). Buttons: **Pause / Resume** (resuming shifts the
remaining dates), **Send now** (hands the next email over even on the same day), **Stop** (cancels the rest, lead to nurture).

## What stops the remaining emails

Unsubscribe, hard bounce, a reply that needs a human (interested, question, and so on; the lead becomes
engaged and sales get a Teams alert, nothing is sent back automatically), a WhatsApp reply, a booking. An
out-of-office reply is ignored. Clicks and visits never stop or change anything.

## Running it

```bash
EMAIL_FLOW=p0 ENV_FILE=.env.demo scripts/run_local
```

`EMAIL_FLOW` is read from the environment: uvicorn's `--env-file` loads the file with python-dotenv
without overriding variables already set, and the settings read the environment first, so the shell value
wins and you do not edit the env file. P0 needs our own sender (`EMAIL_MODE` mailpit, gmail or mailbox);
Instantly stays on the classic flow. The screens exist only with `APP_ENV=local` and have no login until the new frontend.
