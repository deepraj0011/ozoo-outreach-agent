# Go-live checklist

Nothing below is done yet. Do not send to real prospects until all boxes are ticked. Sources: `docs/tool-capabilities.md`, the ledger's deferred minors, and spec section 18.

## A. Start these now (long lead times, spec section 18)

- [ ] Sending inboxes on a **separate domain** (for example ozoo-mail.ai); start warm-up (2-3 weeks). Never cold-email from `ozoo.ai`.
- [ ] DNS: SPF, DKIM, DMARC, plus a CNAME for the tracking domain.
- [ ] Cold-email tool account (Instantly) with API and webhooks.
- [ ] Cal.com: PM demo and sales call event types.
- [ ] API keys: Anthropic and email verification (ZeroBounce).
- [ ] Public HTTPS host for webhooks (Render, Railway or VPS), plus `go.ozoo.ai` pointing at it.
- [ ] Meta Business verification, and **submit WhatsApp templates** for approval (approval takes days).
- [ ] **Approval: cold email to EU / EEA leads** (GDPR legitimate-interest check). Until approved, leave `EU_EMAIL_APPROVED=false`: EU leads are held, no email. After sign-off set it to `true`; the worker releases the holds.
- [ ] **Approval: website tracking script** (cookie consent). The script stays off until approved.

## B. Email tool (Instantly): confirm on a trial account

- [ ] Re-capture every fixture in `tests/fixtures/` from real webhooks (all have `_source`; "constructed" ones are guesses). Update adapter mappings after the first real webhook of each type.
- [ ] C3: per-lead pause/resume. Today it is not implemented and raises an alert asking for a manual pause. Until verified, **pause or resume leads by hand in the Instantly UI** when an `integration_error` alert asks.
- [ ] C5: webhook signing (today a shared-secret header is assumed), hard vs soft bounce field, step number in `sent`, open IP and user agent.
- [ ] C6: custom tracking domain on and tool link tracking **off**. Turn the tool's **open pixel off** too: opens are never used for decisions, so the pixel only hurts deliverability.
- [ ] C1 / C2 / C4: custom-variable field limits, whether in-flight steps pick up updated variables, thread reply by `email_id`.
- [ ] Event-id collision: confirm the tool sends a timestamp or stable id (ledger minor from Task 6).
- [ ] If C3 or C6 fails on trial, switch `TOOL_NAME` to `smartlead`.

## C. Cal.com

- [ ] K1: routing form routes to event types (high to PM demo, medium to sales call, low to thank-you page).
- [ ] K3: `metadata.lead` round-trips on booking links. Fallback is attendee email.
- [ ] Webhook to `/webhooks/cal`; 24 h and 1 h reminder workflows set in the Cal.com UI.

## D. WhatsApp (Meta)

- [ ] Submit and get approval for `ozoo_intro` (buttons `BOOK_DEMO`, `ASK_QUESTION`) and `ozoo_reminder`.
- [ ] Send `hello_world` from a real test number (W1).
- [ ] Record the real error codes 131026, 131049, 131047, 131050 (W4).
- [ ] Confirm the quick-reply tap shape (`type: "button"`, W5).
- [ ] Webhook to `/webhooks/whatsapp`, subscribed to `messages`; test recipients registered (up to 5 on the test number).

## E. Deploy and runbook (Task 13)

- [ ] Disable or redact uvicorn access logs (they record `/r` and `/u` tokens). Document X-Forwarded-For trust.
- [ ] App logging keeps `httpx` at WARNING or higher (no email or key in logs).
- [ ] Microsoft Teams: a team member creates the Workflows flow "When a Teams webhook request is received → post card to the sales channel" and puts its URL in `TEAMS_WEBHOOK_URL` (keep it secret; the flow stops if its owner leaves).
- [ ] Teams admin allows outgoing webhooks / custom apps in the sales team (some tenants block them).
- [ ] In the sales team: Manage team → Apps → Create an outgoing webhook named `OzooAgent`, callback `https://<host>/teams/commands`; copy its security token into `TEAMS_OUTGOING_SECRET`.
- [ ] Set `TEAMS_TENANT_ID`, `TEAMS_SALES_CHANNEL_ID`, `TEAMS_ALLOWED_AAD_IDS` (sales people's Entra object ids).
- [ ] Verify on the real tenant: `@OzooAgent help` replies; signature passes; `channelData.channel.id` is present; a test alert card renders and returns 202; lookalike characters display acceptably.
- [ ] Worker running (`RUN_WORKER=1`) and retrying Teams alerts with `posted_at` empty (Task 12).
- [ ] Postgres in production (SQLite workarounds are not applied there). Plan a migration tool; the `Alert.posted_at` column has none.
- [ ] Marketing replaces placeholder email and WhatsApp copy (`# MARKETING: replace`) and fills `kb/ozoo_kb.md`.
- [ ] Add observed scanner IP ranges to `config/scanners.yaml`; load it with an absolute path.
- [ ] Run the e2e checklist with 3-5 test inboxes and 2-3 test WhatsApp numbers. Every row passes.
- [ ] Re-run the Task 6 reviewer's follow-ups once the final review closes.
- [ ] Daily manual check of Google Postmaster spam rate (not automated in v1).
