# Real demo: real email and real WhatsApp, free

This runs the agent on your laptop against **real services on free plans**. Your manager gets a
real email in their inbox, replies from their phone, and the agent reads the reply with AI and answers.
On WhatsApp they get the real intro message with buttons, chat with the bot, and a salesperson can
take over.

| Piece | Free service | Limit that matters for a demo |
|---|---|---|
| Email out + replies in | A Gmail account (SMTP + IMAP, app password) | ~500 emails/day; personal sender |
| WhatsApp | Meta WhatsApp Cloud API **test number** | Sends only to up to 5 phone numbers you register |
| AI (reads replies, chatbot) | Groq free API | Rate limits, fine for a demo |
| Public address for webhooks | Cloudflare quick tunnel (`cloudflared`) | The address changes each time it starts |
| Sales alerts | Shown in the console (Teams in production) | — |

Your normal local setup (`.env.local`, `local.db`) is not touched: the demo uses `.env.demo` and `demo.db`.
**Send only to inboxes and phones you own, and to your manager with their OK.**

---

## 1. Accounts and keys (about 30–40 min, once)

### Gmail (5 min)
1. Use a Gmail account for the demo (a new one is best, e.g. `ozoo.outreach.demo@gmail.com`).
2. Google Account → **Security** → turn on **2-Step Verification**.
3. Google Account → search **App passwords** → create one named "Ozoo agent". Copy the 16 characters.
4. In `.env.demo`: `GMAIL_ADDRESS=` the address, `GMAIL_APP_PASSWORD=` the 16 characters.

### Groq AI key (2 min)
1. Sign up at <https://console.groq.com> (free, no card).
2. **API Keys** → Create. In `.env.demo`: `LLM_API_KEY=` the key.

### Tunnel (2 min)
Install `cloudflared` (one file, no account). In this terminal type:

```bash
! mkdir -p ~/.local/bin && curl -sSfL -o ~/.local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x ~/.local/bin/cloudflared
```

Start it in its own terminal and leave it running:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

It prints `https://<random-words>.trycloudflare.com`. In `.env.demo`: `LINK_BASE_URL=` that address.
(If you restart the tunnel, the address changes: update `LINK_BASE_URL` and the Meta webhook URL.)

### WhatsApp test number (20–30 min)
1. <https://developers.facebook.com> → log in with Facebook → **My Apps → Create app** → type **Business**
   → add the **WhatsApp** product. Meta creates a free test number and a test business account.
2. **WhatsApp → API Setup**:
   - Copy **Phone number ID** → `WA_PHONE_NUMBER_ID=`.
   - Click **Generate access token** → `WA_ACCESS_TOKEN=` (it lasts 24 hours: make it on demo day).
   - Under **To**, add your phone and your manager's phone (each gets a code to confirm). Max 5.
3. **App settings → Basic** → **App secret** → Show → `WA_APP_SECRET=`.
4. **Create the two templates**: WhatsApp Manager (link on the API Setup page) → **Message templates →
   Create template**. Approval for these usually takes minutes, sometimes a few hours, so do this first.

   **`ozoo_intro`**: category *Marketing*, language **English** (code `en`).
   Body (variables are filled by the agent):
   ```
   Hi {{1}}, thanks for your interest in Ozoo.ai. We help teams like {{2}} follow up with every lead automatically. Would a short demo be useful?
   ```
   Sample values: `{{1}}` = Priya, `{{2}}` = Northwind.
   Buttons → **Quick reply**, in this order: `Book a demo`, `Ask a question`.

   **`ozoo_reminder`**: category *Marketing*, language **English** (`en`).
   ```
   Hi {{1}}, just checking in. Happy to show you Ozoo.ai in a quick call whenever suits you.
   ```

5. **WhatsApp → Configuration → Webhook → Edit** (the app must be running, see step 2 below):
   - Callback URL: `https://<your tunnel>/webhooks/whatsapp`
   - Verify token: the `WA_VERIFY_TOKEN` value already in `.env.demo`
   - Verify and save, then **Manage** webhook fields → subscribe to **messages**.

### Optional: real booking link (5 min)
Make a free <https://cal.com> account and a "Demo" event. In `.env.demo`, put its link as `"booking"`
in `LINK_TARGETS`. Without it, booking links go to an example page and the console's "Lead books a
meeting" button plays the booking.

---

## 2. Start the demo

Terminal 1 (tunnel, from above): `cloudflared tunnel --url http://127.0.0.1:8000`

Terminal 2 (agent):
```bash
# stop scripts/run_local if it is running (both use port 8000)
ENV_FILE=.env.demo scripts/run_local
```
It prints `gmail sender started`. If a key is missing, it names it and stops.

Open the console: <http://127.0.0.1:8000/demo>. The top bar shows **Email: Gmail (real)**,
**WhatsApp: WhatsApp (real)** and **AI: AI model**.

---

## 3. Run the demo (about 10 minutes)

Use **Add lead** in the console. Add your manager twice, as two leads, so both channels show:

| Lead | Fill in | What happens within ~10 s |
|---|---|---|
| Email lead | name, company, **their email**, no phone | Email 1 arrives in their real inbox |
| WhatsApp lead | name, company, **their WhatsApp number** (one of the 5 registered, with country code, e.g. `+9198…`), tick *opted in to WhatsApp*, no email | The `ozoo_intro` message with two buttons arrives on their phone |

Then let them play the lead, on their own phone:

1. **Reply to the email** ("Sounds interesting, can we talk?"). Within ~10 s: the console shows
   *Email reply received · AI: interested*, the status goes to **Qualifying**, and they get a reply in the
   same thread with the booking link.
   Try another reply wording too ("not now, maybe March" → **Nurture**; "remove me" → **Unsubscribed**,
   nothing more is ever sent).
2. **Click a link in the email** (wait one minute after it arrived: earlier clicks look like a security
   scanner and are ignored on purpose). The pricing link raises the score and posts a *hot lead* alert.
3. **WhatsApp: tap "Ask a question"**, then type a question. The bot answers from `kb/ozoo_kb.md`.
   Ask about price → the bot hands off to a person and alerts sales.
4. **Salesperson takes over**: in the console's purple box, type a message and **Take over & send**. It
   arrives on their WhatsApp from the same number; the bot stays quiet. **Hand back to bot** after.
5. **WhatsApp: tap "Book a demo"** → they get the booking link button. Book (or press **Lead books a
   meeting** in the console) → **Meeting booked** and an alert.
6. **Follow-ups**: press **+4 days** in the console → Email 2 goes out to the real inbox.

Everything they did shows in the console: timeline, WhatsApp transcript, alerts.

## How email tracking works (and how far to trust it)

| Signal | How it is recorded | How reliable |
|---|---|---|
| **Reply** | Read from the Gmail inbox over IMAP, classified by the AI | Fully reliable: this drives the flow |
| **Link click** | Every link goes through our signed `/r/` address, then on to the real page | Reliable once scanners are filtered (below) |
| **Unsubscribe** | Only when the person presses **Confirm** on our page, or uses Gmail's own Unsubscribe button (one-click header) | Fully reliable |
| **Open** | A 1x1 image in the email (turn off with `EMAIL_OPEN_PIXEL=false`) | Rough: logged only, never changes score or flow |

**Scanners.** Company mailboxes (Microsoft 365, Mimecast, Proofpoint, ...) open every email and visit
every link, unsubscribe included, seconds after delivery. The agent marks these "security scanner,
ignored" by their address (`config/scanners.yaml`, which lists Microsoft's Safe Links ranges), by
timing (within 60 s of sending), and by pattern (several links at once). Visiting the unsubscribe
link only shows the confirm page, so a scanner can never unsubscribe anyone. A scanner from a new
range can still look human at first: check its address and add the range (owner checked at
rdap.arin.net) to `config/scanners.yaml`.

**Opens are rough on purpose.** Gmail loads images through Google's proxy and often fetches them once
on delivery (marked "scanner"), so a later real open may not reach us; Gmail never loads images in
Spam; Apple Mail opens everything automatically. Use replies and clicks for decisions.

## Follow-ups

- Email 1 starts a thread; emails 2-4 are sent as replies in that same thread ("Re: ..."), like a
  person following up. Any reply, booking, confirmed unsubscribe or bounce stops the sequence at once.
- The schedule is day 0 / 4 / 9 / 15. The demo speeds it up with `TIME_SCALE` in `.env.demo`:
  `TIME_SCALE=288` makes a day 5 minutes (emails at 0, 20, 45 and 75 minutes); `1440` makes a day
  one minute; remove it for real timing.
- When someone at a company books, outreach to their colleagues at the same company pauses.

## Landing in the inbox, not Spam

What the agent already does: plain-text email with a simple HTML copy, one-click unsubscribe headers
(Gmail and Yahoo expect them), follow-ups in one thread, a visible unsubscribe link, no attachments.

What the demo cannot fix, and production must:
- **Links on `trycloudflare.com`.** Spam filters distrust this shared tunnel domain, and every tracked
  link and the open image use it during the demo. Production uses your own domain (for example
  `go.ozoo.ai`).
- **A personal Gmail sender** with no history. Production sends from a separate domain with SPF,
  DKIM and DMARC records, warmed up slowly (about 20 emails a day per mailbox at first).
- **First emails from a new sender** often land in Spam. For tests, mark the first one "Not spam" or
  add the sender to contacts.

## Before the demo: fill the chatbot's facts

`kb/ozoo_kb.md` is placeholder text, so the bot hands most questions to a person. Add 5–10 real lines
(services, who it is for, languages, how a pilot works) so it answers well. Any price question always
goes to a person by design.

## If something does not work

| Problem | Fix |
|---|---|
| App will not start: `must not be blank: …` | Fill that value in `.env.demo` |
| No email arrives | Check spam. The sender log says `gmail SMTP not reachable … Check GMAIL_ADDRESS / GMAIL_APP_PASSWORD` if the login failed |
| Email reply not picked up | Reply from the same address the lead was added with. Replies are checked every 5 s |
| WhatsApp intro not received | The number must be in the API Setup **To** list; the template must be **Approved**; the token lasts 24 h |
| Button taps / messages do nothing | Webhook not verified or `messages` not subscribed; tunnel address changed |
| Webhook verify fails in Meta | The app must be running, and the verify token must match `WA_VERIFY_TOKEN` exactly |
| AI errors in the log | Check `LLM_API_KEY`; Groq rate limit → wait a minute |

To start over: stop the app, then `rm -f demo.db demo.db-wal demo.db-shm .clock-demo.json .gmail_tool.db`
and run `.venv/bin/python -m outreach.dev setup --env-file .env.demo --db demo.db` (keeps `.env.demo`).
