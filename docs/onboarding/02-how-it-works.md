# 2. How it works: a lead's journey

> Previous: [1. What is this repo?](01-what-is-this-repo.md) · Then: the [local testing guide](../guides/local-testing.md)

## Step 1: A lead comes in

Leads arrive in one of two ways:
- **Import:** CSV / Excel through the web console.
- **API:** `POST /intake` with an API key.

Before anything is sent, the agent checks:

| Check | What happens if it fails |
|---|---|
| Already unsubscribed / on the block list? | Rejected. Never contacted. |
| Duplicate of an existing lead? | Skipped. |
| Email address real? (Reacher / ZeroBounce) | Invalid: no email. Catch-all / unknown: still sent. Checker down: held and retried. |
| EU lead and GDPR not signed off yet? | Email held. |
| Phone number valid, and WhatsApp consent given? | No WhatsApp. A number without `+` is read in the lead's country, else `DEFAULT_PHONE_REGION` (default India). |

Then the agent picks the channel: **email**, **WhatsApp**, or both. If a lead has both, WhatsApp goes first by default (`PRIMARY_WHEN_BOTH`).

## Step 2: Email: 4 emails, then stop

| Email | Day | Note |
|---|---|---|
| 1 | 0 | First touch |
| 2 | 4 | Same thread ("Re: …") |
| 3 | 9 | Same thread |
| 4 | 15 | Same thread, the last one |

**How it is sent (`EMAIL_MODE=mailbox`).** A separate program checks every 5 s for emails that are due and sends them over SMTP from our own mailboxes. `scripts/run_local` starts it only for `mailpit` / `gmail`; for `mailbox`, run it next to the app yourself (details in the [mailbox mode guide](../guides/mailbox-mode.md)):

```bash
.venv/bin/python -m outreach.mailpit_sender --env-file .env --base-url https://<your app>
```

- **Several mailboxes.** A new lead goes to the mailbox with the most room left today. The lead stays on that mailbox for life.
- **Warm-up.** Each mailbox sends 10 a day at first, +2 a day, up to 30.
- **Gap.** At least 60 s between two sends from the same mailbox.
- **Every email has:**
  - our tracked links;
  - an unsubscribe link plus the one-click unsubscribe header (RFC 8058);
  - a hidden "trap" link that only scanners follow (see the [mailbox mode guide](../guides/mailbox-mode.md)).

**The sequence stops immediately** when the lead does any of these:
- replies;
- books a demo;
- unsubscribes;
- bounces;
- replies on WhatsApp.

**If nobody replies by day 20** (5 days after email 4), the lead moves to "nurture" (no more emails for now):

| Situation | What happens |
|---|---|
| High interest score | Sales gets a Teams alert to reach out personally |
| Clicked something | Warm list, follow up in 2–3 weeks |
| Silent | Monthly list |

## Step 3: WhatsApp

1. **Intro template** (approved by Meta) with two buttons: **"Book a demo"** and **"Ask a question"**.
2. **No answer for 48 h:** one reminder template.
3. **Still no answer 48 h after the reminder:** switch to email, or stop.
4. **"Book a demo" tapped:** the lead gets a booking button.
5. **"Ask a question" tapped, or any typed message:** the **AI chatbot** answers from `kb/ozoo_kb.md`.
6. **A person takes over when:**
   - the bot has replied 6 times;
   - the lead types HUMAN;
   - the AI is unsure.

   Sales can type in the console or use `@OzooAgent wa <id> <message>` in Teams.

WhatsApp rules are built in:
- free text only within 24 h of the lead's last message;
- templates otherwise;
- STOP / "unsubscribe" means opted out on both channels.

## Step 4: A reply comes in, and AI reads it

Our inbox watcher (IMAP IDLE) sees a reply **within seconds**. It is matched to the lead by:
1. the sender's address;
2. or the hidden thread headers (`In-Reply-To` / `References`), so a reply from a different address still matches.

Automatic replies ("I'm on leave", RFC 3834 headers) are **always** treated as out-of-office, never as interest.

The AI gives each reply one label, and the agent acts on it:

| Label | What the agent does |
|---|---|
| **interested** | Sends the booking link and alerts sales in Teams (hot lead). |
| **question** | Email: alerts sales with an AI-drafted answer. WhatsApp: the chatbot answers. |
| **not now** | Nurture until the date they gave (or a default). |
| **not interested** | Closed. |
| **unsubscribe** | Blocked on all channels, forever. |
| **out of office** | Paused until their return date, then continues. |
| **wrong person** | Closed. If they named the right contact at the same company, that person is added as a new lead. |
| **unsure** | A person reviews it (Teams alert, or chat takeover on WhatsApp). |

Each reply is classified by a background job, so a slow AI never blocks anything. If the AI fails, it retries and then alerts a person.

## Step 5: Booking and sales

The lead books on Cal.com, and Cal.com sends us a signed webhook. Then:
- the lead becomes **booked**;
- all outreach stops;
- sales gets a Teams card.

Reschedule, cancel and no-show are handled the same way.

Sales can also act from Teams:
- `@OzooAgent status 12`
- `close 12`
- `nurture 12`
- `wa 12 <msg>`
- `done 12`

## The lead's life stages (`states.py`)

```
intake ─► in_sequence ─► engaged ─► qualifying ─► booked ─► handed_off
   │           │  ▲          
   │           ▼  │          
   │         paused (OOO)     nurture (not now / no reply)    closed
   ▼                                                        
rejected                  suppressed (unsubscribed / bounced)   archived
```

Only allowed moves are possible. They're listed in a table in `states.py`, and any other move is refused and logged.

## How the code makes decisions (for developers)

Every outside event goes through **one pipeline** (`ingest.py`):

```
webhook / tracked link
   → check signature, size limit
   → store event (duplicates skipped, so provider retries are safe)
   → find the lead (lead id, address, phone, or company domain)
   → robot check (botfilter.py)  — only "human" events change anything
   → rules.decide()   ← pure function: lead + event in, list of actions out
   → actions.execute() ← state changes, sends, alerts
   → commit → Teams alert after commit
```

- **`rules.decide()` has no database and no network**, so every business rule is a simple, fast unit test (`tests/test_rules.py`, 263 tests).
- **Outside services sit behind "ports"** (interfaces). A failing service becomes an `integration_error` row and an alert; it never crashes the flow.
- **The worker** (`worker.py`) runs time-based jobs in order. Each job uses its own transaction and locks the lead row, and only one worker runs at a time. Running the same tick twice does nothing extra.

## Check it yourself in 2 minutes

```bash
scripts/local_setup && scripts/run_local            # terminal 1
.venv/bin/python -m outreach.dev seed               # terminal 2: a few fake leads
.venv/bin/python -m outreach.dev tick               # starts them (email 1 "sent")
.venv/bin/python -m outreach.dev sim email-reply --lead 1 --text "Sounds good, let's talk"
.venv/bin/python -m outreach.dev show 1 --mask      # state: qualifying, booking link sent
.venv/bin/python -m outreach.dev advance --hours 100 && .venv/bin/python -m outreach.dev tick   # time travel: WhatsApp reminders go out
# In fake email mode the email tool's own schedule is not simulated: announce emails 2-4 with
# .venv/bin/python -m outreach.dev sim email-sent --lead 5 --step 2
```
