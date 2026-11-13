# 10-minute demo script

Everything happens in one web page. No terminal during the demo.

## Before the meeting (5 min)

1. Stop any running agent (Ctrl+C in its terminal).
2. Optional, to start from an empty screen: `rm -f local.db local.db-wal local.db-shm .local_clock.json .mailpit_tool.db`
3. Start: `scripts/start_demo`. It starts the agent (and Mailpit if your settings use it) and opens
   <http://127.0.0.1:8000/> in the browser.
   - With real Gmail + WhatsApp (docs/REAL_DEMO.md): `scripts/start_demo .env.demo`
4. Have `docs/sample_leads.xlsx` ready (or your own CSV / Excel file).

## The story

**1. "This is how the agent works."** (1 min) Open **How it works**. Walk the 5 steps: intake check →
outreach → reply handling → booking → hand to sales, plus the nurture loop. Its job ends when a qualified
meeting is handed to sales.

**2. "We give it leads."** (1 min) **Leads → Import CSV / Excel** → drop `docs/sample_leads.xlsx`.
The preview shows which columns it matched and flags the row with no contact details. Click **Import**:
the result shows who was accepted, the EU lead held for legal approval, and the skipped row.

**3. "It contacts them by itself."** (1 min) **Overview**: leads move to *In outreach* within seconds.
Email leads get Email 1; WhatsApp leads get the approved intro message (see **Conversations**).

**4. "It reads replies with AI."** (2 min) **Leads** → click *Priya Shah* → **▶ Simulate the lead** →
**Lead replies** ("Sounds interesting…"). The step tracker jumps to *Interested*, the activity shows
*AI read it as: Interested*, and a booking link goes back. Try "Not now" on another lead → *Nurture*.
"Remove me" → *Do not contact*, never contacted again.

**5. "Buying signals alert sales."** (1 min) On an email lead, **Clicks pricing link** → engagement goes up
and **Sales alerts** shows a *Hot lead*. **Security scanner click** is marked *bot, ignored*.

**6. "WhatsApp: chatbot and a human."** (2 min) **Conversations** → *Arjun Mehta*. Demo box: send
"Which languages do you support?" → the bot answers. Send "What would a custom price be?" → the bot
stops and alerts sales. Type in **Reply as a salesperson** → the message goes out, the bot stays quiet.
**Hand back to bot** when done.

**7. "Booking and handoff."** (1 min) Open *Sara Lopez* → **Books a meeting** → *Meeting booked* + alert.
**Meeting held** → *Handed to sales*, with a full summary in the alert.

**8. "Follow-ups happen over days."** (30 s) **Settings → +4 days**: Email 2 goes out to leads that have
not replied. The **Overview** funnel shows the whole picture.

## If something looks stuck

- Nothing happens: **Settings → Run the agent now** (it also runs every 10 s by itself).
- The bot says "a member of our team will reply" to every question: the knowledge base (`kb/ozoo_kb.md`)
  is placeholder text. Add a few real lines, e.g. `- Ozoo.ai works in English and Hindi.`
- **Settings → Back to today** resets the time machine.
