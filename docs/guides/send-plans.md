# Email schedule: when each email goes

The **Email schedule** screen in the web app lets the product manager decide **when** each email goes
and **what type** it is. The agent still writes every email for the lead's role and company.
No passcode: every change is written to the lead's History.

## The screen

One schedule for every new lead, read top to bottom:

```
(1) Email 1   Type: Use case + demo button   Sends: as soon as the lead is added | N days after | on a date
(2) Email 2   Type: Agent picks              Sends: 4 days after email 1
              [x] ⚡ Sooner if they show interest: 2 days, as Case study
(3) Email 3   Type: Agent picks              Sends: 5 days after email 2
(4) Email 4   Type: Agent picks              Sends: 6 days after email 3
(■) Stop      5 days after the last email (the lead moves to nurture; HOT leads go to sales)
```

- **Type:** Use case + demo button, Case study, Product screen, Pick a meeting time, or *Agent picks*
  (from what the lead did). **Preview** shows it as a sample lead would get it.
- **Sends:** days count from when the previous email was actually sent; the email goes in the next
  working-hours slot. "On a date" for email 1 goes at that time (Asia/Kolkata, `BUSINESS_TZ`).
- **⚡ Sooner if they show interest** (optional per email): if, since the previous email, the lead
  clicked a link, read a page on our site, or became HOT, that email goes after the shorter wait and
  as the type picked there. Security scanners, the unsubscribe link and email opens never count.
  Tick **"and make it the last email"** to end the sequence after that email when the lead showed
  interest (e.g. send "Pick a meeting time" and let sales take it from there).
- **Interest after an email is queued** (e.g. decided in the evening for the morning slot) still
  switches it to the "sooner" type before it goes. Your own per-lead changes are never overridden.
- **+ Add email / Remove:** 1 to 4 emails.
- The **Example** box shows the dates for a lead added today.
- **Always:** a reply, booking, unsubscribe or bounce stops the emails at once, even an email already
  queued. The agent then handles the reply: interested = booking link, question = an answer from the
  knowledge base, not interested / wrong person = a polite close, unsubscribe = opt-out.
- **"Not now, try in March":** the lead waits in nurture; on that date it gets one check-in email in the
  same thread (`replies.nurture_checkin` in `templates/emails.yaml`). A reply goes through the normal flow.
- **Out of office:** the emails pause until the return date; then the next email goes no sooner than one
  day after the lead is back.

Saving applies to new leads, and to each current lead's next email that is not decided yet.
Until the schedule is saved once, leads get the fixed days 0, 4, 9, 15.

## Per lead: the Email schedule tab

A lead's panel shows the emails sent (✓, "⚡ sent sooner" when interest brought one forward), a
"👀 showed interest since email N" line, one **Next** card and the emails still to come. The Next
card has four buttons:

| Button | What happens |
|---|---|
| Send now | The next email goes within seconds |
| Change date | Pick a date and time for the next email |
| Pause / Resume | Nothing goes until you resume |
| Stop emails | No more emails; the lead moves to nurture |

A change affects only that lead's next email ("Undo my change" puts it back). A lead added before
the schedule was saved shows "Fixed days" and a **Switch to the email schedule** button.

## Import

When you import leads, email 1 goes as the schedule says, or pick "on a date".

## Upcoming emails

The second tab lists every lead's next email by day: *Scheduled* (handed to the sender), *Planned*
(interest can still bring it forward), *Paused*, or *Fixed days*. Click a row to open the lead.

## Limits

- Needs our own sender: `EMAIL_MODE=gmail`, `mailbox` or `mailpit`. With Instantly
  (`EMAIL_MODE=real`) the Instantly campaign's own schedule applies.
- Code: `src/outreach/send_plan.py` (schedule, decisions, changes), worker job `plan_schedule`,
  routes in `schedule_console.py`, screen in `demo_schedule.js`.
