# P0: scheduled emails, chosen by the PM

**Date:** 2026-11-20 · **Status:** approved ("build the P0") · **Approach:** B (a small, separate P0 schedule)

> §6 holds the answers to the builder check; where §6 and §4 differ, §6 wins.

## 1. Why

The agent got hard to understand: the PM sets an email *type*, the agent then picks the text by role,
"⚡ sooner if interested" moves dates, and even the owner could not say which email a lead would get
when. The work is split in two:

- **P0 (this doc):** a lead is added → it gets the emails the PM scheduled, on the days the PM set.
  Fully predictable. Tracking is recorded and shown, never acted on.
- **P1 (later):** trigger points (clicks, opens, visits, interest) that change what is sent and when.

A better frontend is a separate step after P0. P0 builds everything behind a clean JSON API so that
the later frontend only replaces screens.

**Success:** the PM sets "template A on day 0, B on day 3, C on day 7"; a lead added today gets exactly
A today, B in 3 days and C in 7 days, in working hours; the lead panel shows that list in advance and
ticks each one off as it is sent, with its opens and clicks; an unsubscribe stops the rest.

## 2. Goals, non-goals, anti-goals

**Goals**
1. A **template library** the PM manages: add, edit, preview, make live, switch off. No passcode.
2. **One schedule**: an ordered list of *email #, template, Day N*.
3. **Each lead's planned emails** are written when the lead starts, so "what will this lead get, when"
   is a visible list.
4. The worker sends due emails in working-hours slots through our own sender.
5. **Tracking always on**: opens, clicks and page visits are recorded against the sent email and
   shown. They change nothing.
6. **Stops:** unsubscribe and bounce (and a reply, see §4.5) cancel the lead's remaining emails.
7. Per-lead controls: Pause / Resume, Stop, Send next now.

**Non-goals (P0)**
- Trigger logic of any kind (sooner if interested, agent picks, branch on click). → P1
- Role-based template versions or AI-written / AI-chosen copy. → P1
- Automatic replies to the lead (booking link, KB answer, polite close). → P1
- WhatsApp. It keeps working as it does today and is not touched by P0.
- Instantly (`EMAIL_MODE=real`). P0 needs our own sender, as the current schedule already does.
- The new frontend.

**Anti-goals (a failure even if it "works")**
- A lead receives anything other than exactly the template the PM scheduled.
- An open or click changes what is sent, when, or alerts anyone.
- Any email to a lead after their unsubscribe or bounce.
- The PM cannot tell, in advance, which email a lead gets on which date.

## 3. Approaches considered

| | Approach | Trade-off | |
|---|---|---|---|
| A | Trim the existing `send_plan.py` (drop "Agent picks" and "sooner", type → template, days from add) | Least new code, but the old plan conversion, per-lead change flags and variant × role template slots stay underneath | not picked |
| **B** | **A small, separate P0 schedule: templates, schedule, per-lead planned-email rows; the worker sends due rows** | More new code at first; the old path stays, switched off. In return, every lead's future is a plain list you can query, show and test | **picked by the owner** |
| C | Use Instantly's own campaign schedule | The PM would manage the schedule in Instantly, not our app | dropped |

## 4. Design (decided)

### 4.1 Templates
- Fields: name, subject, body (text with placeholders), status `draft | live | off`, created / updated by and when.
- Placeholders: `{first_name}`, `{name}`, `{company}`, `{role}`, plus the booking link. The unsubscribe line is
  **added automatically** to every email (it cannot be left out).
- No step, variant or role slot: any live template can be put on any email.
- No passcode. Every change is written to History with who did it.
- A template used by the schedule cannot be switched off or deleted until it is replaced there
  (the screen says which email uses it).
- **Starter templates** *(builder's call, see §5)*: the 4 standard emails in `templates/emails.yaml` are
  copied in once as live templates, so the PM starts with a working schedule.

### 4.2 Schedule
- One schedule for every new lead: rows of *email #, template, Day N*. Email 1 is normally Day 0.
- Day N counts from **when the lead was added**, not from the previous email. Days must increase down the list.
- 1 to 10 emails.
- Every email goes in the next working-hours slot on or after its day (existing `sendwindow.py`, Asia/Kolkata).
  A lead added after hours gets email 1 in the next slot.
- **Editing the schedule** applies to new leads, and to current leads' emails that are not sent yet:
  their planned rows are rewritten from the new schedule (same Day N maths from their add date).
  Emails already sent are never changed. A planned day that is now in the past goes in the next slot.

### 4.3 Each lead's planned emails
- When a lead starts, one row per schedule email is written: lead, email #, template (and the template
  version used), due date, status `planned | sent | cancelled | failed`, sent at, and its tracking counts.
- The lead panel shows this list: ✓ sent (date, opened / clicked), next, still to come, cancelled (and why).
- The worker sends rows that are due, one at a time per lead, with the same idempotency rules as today
  (a guard written in the same transaction, so a repeated tick never sends twice).

### 4.4 Tracking (record only)
- Opens, link clicks and verified page visits are stored as today (bot filtering included) and linked
  to the sent email. They are shown on the lead and the overview.
- In P0 they do **not** move dates, change templates, change the lead's state, or send Teams alerts.

### 4.5 What stops a lead's emails
| Event | P0 behaviour |
|---|---|
| Unsubscribe | Remaining rows cancelled, address blocked, as today. |
| Bounce | Remaining rows cancelled (the address does not exist; sending again hurts the sender). |
| Reply from the person | Remaining rows cancelled; lead marked **Replied**; sales alerted in Teams; **no automatic reply**. Out-of-office and bot replies do not count (existing `botfilter.py`). |
| Booking | Remaining rows cancelled (they already said yes). |

### 4.6 Per-lead controls
Pause / Resume (planned rows wait; on resume, past-due rows go in the next slot), Stop (cancel the
rest), Send next now. Each is written to History.

### 4.7 Old path
The current sequence (`sequencer.py` 4-step flow, `send_plan.py`, the Templates screen's variant/role
slots) stays in the code, switched off by a setting while P0 is on. P1 decides what of it comes back.
Leads already in the old sequence finish on the old path.

### 4.8 Screens (simple now, rebuilt later)
- **Templates:** list (name, status, used by email #), editor with live preview for a sample lead.
- **Email schedule:** the table *#, template ▾, Day N*, + Add email, an Example box with dates for a lead
  added today, Save.
- **Lead panel → Emails tab:** the planned-email list and the 3 controls.
- All three read and write JSON routes only, so the later frontend can replace them.

## 5. Left to the builder
- Table and module names; whether P0 templates reuse the `email_template` table or get a new one.
- How the own-sender tools (`gmail`, `mailbox`, `mailpit`) send an arbitrary email N: today they hold
  a fixed 4-step sequence (`LAST_STEP = 4`) fed by variables, so P0 needs a "send this email now" path
  for up to 10 emails.
- The setting name that switches between P0 and the old path.
- Updating `test_world.py` personas and `test_local_e2e.py` for P0 behaviour.
- Tests first (TDD), ≥ 80% coverage of the new code.

## 6. Builder-check answers (2026-11-20)

Owner's answers (my recommendation, accepted with "build the P0"):
1. **Local only.** P0 screens and routes stay registered only when `APP_ENV=local`, like today's console.
   No login in P0; it comes with the new frontend before anything goes online.
2. **Same thread.** Email 1 uses its template's subject. Emails 2..N go as replies in email 1's thread
   ("Re: <email 1 subject>", In-Reply-To / References), as today; their template subject is shown in the
   editor as "used only when this is email 1".

Builder's calls (made by Claude, owner may correct):
3. **Day 0** = when the lead's emails may start: when intake/verification/hold lets email begin, or the
   start time picked at import, or the moment a WhatsApp lead switches to email. Day N = calendar day N
   in `BUSINESS_TZ` (Asia/Kolkata) counted from Day 0's date; the email goes in the first working-hours
   slot on or after that day (per-lead minute offset kept; no afternoon/morning rotation).
4. **At most one email per lead per calendar day.** When rows are overdue (resume, late start, edit),
   the next one goes in the next slot and the rest keep their original gaps (shifted by the delay).
5. **Template text is taken at send time** (the live version then). The sent row stores a copy of the
   exact subject and body that went out; the panel shows that copy for sent emails.
   Editing a live template puts it back to draft only if the PM asks ("Save as draft"); "Save" keeps it
   live (no passcode, history records the change).
6. **Replies.** The existing classifier still labels replies. Out-of-office: nothing changes, the schedule
   continues. Unsubscribe / "remove me": as an unsubscribe (cancel + block). Any other human reply
   (interested, question, not interested, wrong person, later): cancel remaining rows, lead → `engaged`
   (no new state), one Teams alert, no automatic reply to the lead.
7. **WhatsApp.** P0 schedules apply to leads whose channel is email. A WhatsApp reply or opt-in from the
   same lead also cancels remaining email rows (one lead, one state). A hard bounce still switches the
   lead to WhatsApp if it has a phone (existing rule), after cancelling the rows.
8. **After the last email:** 5 days later the lead goes to `nurture`. No score routing, no alert.
9. **Schedule edits** match current leads' unsent rows by email number, but a template the lead was
   already sent is never sent to it again (that row is skipped, marked "already sent").
10. **Mailbox caps / sender failures:** a row that cannot go in its slot moves to the next slot and the
    panel shows it as **late** (with the original date). After 3 failed tries it is **failed** and the
    lead's next email is not sent until the PM presses Send next now or Stop.
11. **Look and links.** Emails keep the designed HTML look (`richmail.py`) with the PM's text inside, plus
    a plain-text part. Placeholders: `{first_name}`, `{name}`, `{company}`, `{role}`, `{signature}`,
    `{booking_link}`, and site links `{link:<page>}` from the existing link list; all links are tracked
    and site links get the verified-visit token, as today.
12. **Starter templates:** the 4 standard emails of `templates/emails.yaml` flattened to text (general
    angle, first body alternative) and stored as live templates once, with a default schedule
    Day 0 / 4 / 9 / 15.
