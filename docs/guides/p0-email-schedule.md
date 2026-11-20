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
Day 0 is the day the lead is added (or the start time picked at import, if that is still ahead; a start
time already in the past counts as now); Day N is calendar day N in `BUSINESS_TZ` (Asia/Kolkata). The
Example column shows the dates for a lead added now, as the send slot (working hours) the email would
actually go in; **Example dates** recomputes them for the rows on screen without losing unsaved edits.

- Every P0 email goes at the first-email slot of the send window (no afternoon / morning rotation).

- At most one P0 email per lead per calendar day, in the next working-hours slot.
- Email 1 has the template's subject. Later emails go in the same thread as `Re: <email 1 subject>`.
- 5 days after the last email the lead moves to nurture (no alert).
- Editing the schedule changes the lead's emails not yet handed to the sender; one already handed over is not
  rewritten. It applies to every lead with emails still planned (in the sequence, paused or on hold), and
  keeps any shift from a resume or a hold ending (dates stay moved). An email removed from the schedule is cancelled.
- If an email handed to the sender is still not sent 2 days after its slot, sales get one `stuck_lead`
  alert for it ("check the sender").

## Lead panel, Emails tab

Each planned email shows planned / handed / sent / cancelled / skipped, with its date shown as the send
slot it will actually go in. A **late** tag means the email went later than planned (for example a weekend
or a pause). "Emails sent" shows sent / scheduled for P0 leads. Buttons (only for P0 leads in the sequence
or on hold): **Pause / Resume** (pause holds the sender at once; resuming shifts the remaining dates; an
account hold ending shifts them the same way), **Send now** (hands the next email over even on the same
day; it refuses with the reason, and changes nothing, if the lead is paused or on hold, an email is already
waiting in the sender, or a reply is being read), **Stop** (cancels the rest, lead to nurture).

## What stops the remaining emails

Unsubscribe, hard bounce, a reply that needs a human (interested, question, and so on; the lead becomes
engaged and sales get a Teams alert, nothing is sent back automatically), a WhatsApp reply, a booking.
A reply pauses the sender at once, before it is read, so no email goes in between. An out-of-office reply
resumes the sender and keeps the schedule. If the reply cannot be read (classification failed 3 times) the
emails stop (reason `reply_unread`) and sales are alerted to read it. A reply from a colleague at the same
company also stops the emails ("a colleague at <company> replied to lead N's email"). Later replies, after
the lead is engaged, only alert sales; P0 never answers an email automatically. Clicks and visits never stop
or change anything.

## Running it

```bash
EMAIL_FLOW=p0 ENV_FILE=.env.demo scripts/run_local
```

`EMAIL_FLOW` is read from the environment: uvicorn's `--env-file` loads the file with python-dotenv
without overriding variables already set, and the settings read the environment first, so the shell value
wins and you do not edit the env file. P0 needs our own sender (`EMAIL_MODE` mailpit, gmail or mailbox);
Instantly stays on the classic flow. The screens exist only with `APP_ENV=local` and have no login until the new frontend.
