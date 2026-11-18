# Mailbox mode: our own email sender (free, open source)

`EMAIL_MODE=mailbox` sends the sequence from ordinary mailboxes and reads replies back over IMAP. It replaces Instantly with no SaaS fee: you pay only for the mailboxes. Instantly remains an option (`EMAIL_MODE=real`).

## What it does

| | How |
|---|---|
| Sequence | Email 1, then follow-ups on days 4 / 9 / 15, each in the same thread (`Re: …`, `In-Reply-To`). |
| Several mailboxes | Each new lead goes to the mailbox with the most room left today. The lead stays on that mailbox for its follow-ups and replies. |
| Daily cap + warm-up | Day one sends `warmup_start` (10), then `+warmup_step` (2) a day up to `daily_limit` (30). |
| Gap between sends | At least `min_gap_s` (60 s) between two sends from one mailbox. |
| Replies | One IMAP IDLE watcher per mailbox, so replies arrive in seconds. It reconnects every 9 minutes and after any error, so it can't freeze the sender. |
| Automatic replies | Messages with an `Auto-Submitted`, `X-Autoreply` or `Precedence: auto_reply` header are always treated as out-of-office, never as a person engaging. |
| Reply from another address | Matched by `In-Reply-To` / `References` against every Message-ID we sent. |
| Bounces | Read from `mailer-daemon@` / `postmaster@` notices. |
| Unsubscribe | `List-Unsubscribe` + one-click header (RFC 8058) on every email. |
| Clicks | Our signed links. A hidden honeypot link catches scanners, and a JavaScript-confirmed click page tells a browser from a scanner. |
| Opens | Off by default (`EMAIL_OPEN_PIXEL=false`): opens are unreliable and can hurt inbox placement. |

## Setup (production)

1. **A separate sending domain**, e.g. `send-ozoo.com` or `ozoo-mail.com`. Never the main company domain: if it gets a bad reputation, normal company email is unaffected.
2. **Mailboxes on that domain**: Google Workspace (about ₹270 per user per month) or Zoho Mail. 2–3 mailboxes to start.
3. **DNS for the domain**: SPF, DKIM (from the mail provider's admin console) and DMARC (`v=DMARC1; p=none; rua=mailto:…` to start).
4. **An app password per mailbox**:
   - Google: Account → Security → 2-Step Verification → App passwords. IMAP is on by default for Workspace.
   - Zoho: Settings → Security → App passwords, and turn on IMAP access.
5. **Settings**:
   ```
   EMAIL_MODE=mailbox
   MAILBOXES=[{"address":"deep@send-ozoo.com","password":"<app password>","provider":"google"},
              {"address":"team@send-ozoo.com","password":"<app password>","provider":"google"}]
   VERIFY_PROVIDER=reacher        # optional, free: see below
   ```
6. **Run the sender next to the app**:
   ```
   python -m outreach.mailpit_sender --env-file .env --base-url https://<your app>
   ```
   It sends due steps every 5 s, reports each send to the app as a signed webhook, and its IDLE watchers report replies as they arrive.

## Warm-up: the first 2–4 weeks

Mailbox mode has no shared warm-up network, so ramp up slowly. The defaults do this for you (10 a day, +2 a day, up to 30). Also:

- Before the first campaign, send a few normal emails back and forth with colleagues from each mailbox.
- Watch the bounce rate. Keep it under 2%: verify every address first (Reacher or ZeroBounce).
- Keep the spam-complaint rate under 0.1% (Google Postmaster Tools shows it once volume allows).
- Need more volume? Add a mailbox; don't raise `daily_limit` above about 40.

## Reacher (free email verification)

[Reacher](https://github.com/reacherhq/check-if-email-exists) is open source. It checks whether an address exists without sending to it. It needs outbound port 25, so run it on a server, not a home connection (most ISPs block 25, and then every answer is "unknown", which is still sendable).

```
docker run -d -p 8080:8080 -e RCH__HEADER_SECRET=<secret> reacherhq/backend
VERIFY_MODE=real
VERIFY_PROVIDER=reacher
REACHER_URL=http://<host>:8080
REACHER_SECRET=<secret>
```

Mapping: `safe` → valid; `invalid` → invalid; `risky` with a disposable address → invalid; `risky` with a catch-all domain → catch_all; anything else → unknown (sendable). An outage → `verify_error`: the lead is held and the worker retries.

## Limits to know

- Microsoft 365 is retiring basic SMTP AUTH. A Microsoft mailbox may need SMTP AUTH turned on by the admin (or use Google / Zoho instead).
- Caps are counted per UTC day.
- Replies that land in Spam aren't read: the watcher reads INBOX. Check the Spam folder during warm-up.
