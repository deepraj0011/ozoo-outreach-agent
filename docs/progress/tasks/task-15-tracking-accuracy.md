# Task 15: own open tracking, tracking accuracy, inbox placement, threaded follow-ups

**Date:** 2026-11-17. Applies to the modes where we send the emails ourselves (`EMAIL_MODE=mailpit` / `gmail`); Instantly keeps its own tracking.

## Why
Testing with real inboxes showed four problems:
- Gmail-mode emails had no open tracking.
- A Microsoft 365 inbox (Defender Safe Links) "opened" every email and "clicked" every link, unsubscribe included. One scanner click, about 3.5 min after sending, was counted as human and added score.
- Unsubscribe-page visits showed as plain "Link clicked".
- Follow-ups went out as separate emails with new subjects and no unsubscribe header. Some landed in Spam.

## What changed
- **Open pixel.**
  - Each email carries a signed 1x1 image (`/o/<token>.gif`, "O." token prefix). The image is always served.
  - Opens go through the shared ingest path: scanner/too-soon opens are "bot", Apple opens are "machine", and only human opens are counted (`human_opens`).
  - Opens never change score or flow (plan v2.1). Reloads within 5 min count once.
  - `EMAIL_OPEN_PIXEL=false` turns the pixel off.
- **Microsoft Safe Links ranges** are in `config/scanners.yaml`: `74.224.0.0/14` and `4.208.0.0/12`, both MSFT per ARIN RDAP.
- **Unsubscribe-link visits.**
  - The worker now labels them human or bot like any other click, and still never acts on them.
  - The console shows "Unsubscribe page opened" plus a "security scanner, ignored" tag.
  - Only Confirm, or a one-click POST, unsubscribes.
- **One-click unsubscribe headers.** `List-Unsubscribe: <…/u/<token>>` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` go on every send and every reply.
- **Threaded follow-ups.**
  - Steps 2–4 are sent as "Re: <email 1 subject>" with `In-Reply-To`/`References` pointing to the previous message. Each step's own body is kept.
  - Template subjects for steps 2–4 are therefore unused in these modes.
- **Demo timing.** `.env.demo` `TIME_SCALE` changed from 1440 to 288, so a day is 5 minutes and emails go out at 0, 20, 45 and 75 min.
- **Docs.** `docs/guides/real-demo.md` gained three sections: how far to trust each signal, follow-ups, and inbox placement (the `trycloudflare.com` link domain and personal Gmail are the demo's main spam factors).

## Tests
- `tests/test_open_tracking.py`: 23 tests.
- `tests/test_tracking_accuracy.py`: 12 tests.
- Existing tests were updated for the new behaviour (threaded subjects, unsubscribe visits waiting for a verdict, scanner seed list).
- Suite total: 1566 passing.

## Not done (production)
- Own link and tracking domain.
- SPF / DKIM / DMARC and mailbox warm-up.
- Sending window for the Gmail/Postal sender.
- Scanner ranges for other vendors, to be added as they are observed.

## Fix (2026-11-17): Gmail sender froze on a stalled IMAP connection

- Symptom: replies were not picked up and new leads got no email. The sender process was alive but blocked forever reading a Gmail IMAP socket.
- Cause: `imaplib.IMAP4_SSL` was opened without a timeout.
- Fix: `IMAP_TIMEOUT_S = 30` in `emailtool/gmail.py`. A stall now raises `TimeoutError` (an `OSError`), which `run_once` logs; the next pass retries. Regression test: `test_imap_connection_has_a_timeout`.
- Demo note: tracking URLs are signed per (lead id, step). After a DB reset, lead ids repeat, so opening an *old* test email counts against the new lead with the same id. Delete old test emails before a fresh run.
