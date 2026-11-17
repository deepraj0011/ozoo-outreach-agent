# Task 17: free, accurate email layer (mailbox mode + tracking accuracy)

**Why:** a research tournament (2026-11-17) compared our own Gmail sender, Gmail / Graph APIs, cold-email SaaS (Instantly, Smartlead, lemlist), transactional sending services (SES, Mailgun, Postmark, Resend, Brevo) and self-hosted Postal.
- Every transactional service checked bans cold outreach in its terms (SendGrid's couldn't be checked), so they're out.
- Self-hosted Postal fails on port 25 and IP reputation.
- The user chose the free route: our own sender over ordinary mailboxes, plus the accuracy findings.

## Commits

1. **`46d4d63`: accuracy basics.**
   - Open pixel off by default (`EMAIL_OPEN_PIXEL=false`).
   - RFC 3834 automatic replies (`Auto-Submitted`, `X-Autoreply`, `Precedence: auto_reply|bulk|junk`) get `classifier.AUTO_REPLY_MARK` and are always classified `ooo`. The model only reads the return date.
   - Replies from another address are matched by `In-Reply-To` / `References` against every Message-ID we sent (`mp_msgid` table).
2. **`21d4f44`: Reacher**, an open-source self-hosted verifier (`VERIFY_PROVIDER=reacher`, `REACHER_URL`, `REACHER_SECRET`).
3. **`182e571`: scanner-proof clicks.**
   - Honeypot: a hidden `trap` link in every email we send (`EMAIL_HONEYPOT`). A visit is always bot, and the same /24 (/48) network's clicks on that email within 1 h are bot too.
   - JavaScript-confirmed click (`CLICK_JS_CONFIRM`): email links answer with a CSP-locked page (nonce script) that POSTs `/r/<token>/ok?e=<event>` before forwarding.
     - A click with no beacon is bot.
     - The worker waits at least 10 s before judging such a click.
     - WhatsApp links keep the plain 302.
   - The dev CLI and the console's "simulate click" act like a browser and send the beacon; `--scanner` does not.
4. **This commit: `EMAIL_MODE=mailbox`.**
   - Several SMTP / IMAP mailboxes (`MAILBOXES` JSON; presets google / zoho / zoho_in / microsoft / custom).
   - Per-mailbox daily cap with warm-up ramp and a minimum gap. A lead is pinned to its mailbox.
   - One IMAP IDLE watcher thread per mailbox, with reconnect and backoff. The sender loop is woken when a reply arrives.
   - Allowed in production: the sender skips the local-only check for this mode.
   - `EMAIL_MODE=gmail` is now a one-mailbox preset of the same tool (no cap ramp, 400 a day).
   - Guide: [docs/MAILBOX_MODE.md](../../MAILBOX_MODE.md).

Also: the IMAP timeout fix (`2ef16fd`): a stalled Gmail connection froze the sender for an hour.

## Notes

- Opens are now off. In the demo, "Opened" only appears if `EMAIL_OPEN_PIXEL=true` is set again.
- The click page adds a short visible "Opening…" step (about 0.2 s) before Cal.com.
- Microsoft Defender's sandbox runs JavaScript. It is still caught by the honeypot (same network) and the Microsoft IP ranges in `config/scanners.yaml`.
- Not done: identifying scanners by network owner (ASN) instead of IP lists, and parsing RFC 3464 bounce reports (we match the bounce notice text).
- Tests: 1645 passing.
