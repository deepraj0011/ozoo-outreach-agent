# Go-live checklist

Nothing below is done yet. Do not send to real prospects until all boxes are ticked. Sources: `docs/reference/tool-capabilities.md`, the ledger's deferred minors, and spec section 18.

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

## Added 2026-11-13 (Tasks 9b, 11)
- [ ] **AI provider:** choose `LLM_PROVIDER` and the models. Check the provider's data-processing terms and data location, because lead reply text is sent to it. Use a self-hosted model if the data must stay in-house.
- [ ] **Cal.com, real account:** confirm that `metadata[lead]` arrives through the routing form, that a reschedule carries the new booking id, the form answer keys (`response`/`value`), the phone fields and the signature format. Use dropdowns for company size and timeline.

## Added by the final review (2026-11-13)
- [ ] **Instantly trial account.** Confirm:
  - the `sent` webhook carries the step number;
  - which field marks a bounce as hard or soft;
  - the event ids are unique per event;
  - DELETE stops the lead, and a 404 means it is already stopped;
  - the block-list endpoint;
  - lead lookup by email;
  - per-lead and campaign pause.
  
  Also turn on Instantly's own "stop on reply" as a backstop.
- [ ] Add database migrations (Alembic) before the first production database.
- [ ] Production: disable or redact the uvicorn access log (it contains `/r/` and `/u/` tokens). Set `TRUST_PROXY_HEADERS=1` only behind a trusted proxy.
- [ ] A lead paused because a colleague engaged needs a re-enrol path when it resumes, because Instantly has no resume.

## Added 2026-11-18 (task 19: real leads, real tracking)

Before the first batch of real leads:

- [ ] **Run the realistic simulation** and keep the report with the review: `python -m outreach.world --leads 100 --out run.md`. Every outcome must be as expected and "hot-lead alerts / wrong" must be 0.
- [ ] **Website snippet for verified visits:** the web team adds `<script src="https://<tracking host>/v.js" async></script>` to the home, pricing, case-study and product pages, and allows the host in the site's CSP (`script-src`, `connect-src`). See [guides/visit-tracking.md](../guides/visit-tracking.md). This is the "website tracking script" approval in section A: get the privacy sign-off and add the line to the privacy notice first. Until then set `VISIT_TRACKING=false`.
- [ ] **`LINK_TARGETS`** point at the real pages (home, pricing, case study, product, booking). Visits are only measured on pages that carry the snippet.
- [ ] **Keep `CLICK_ALERTS_NEED_CHECK=true`** for the first weeks: clicks alone send a "check first" alert and never email the lead. Review every `click_check` alert's events (`dev show <id> --mask`) and add any scanner range you find to `config/scanners.yaml` (verify ownership on rdap.arin.net first).
- [ ] **Send window** (`EMAIL_MODE=mailbox`): leave `EMAIL_SEND_WINDOW` unset (Mon-Fri 09:30-17:30) and `EMAIL_SEND_TZ` unset (BUSINESS_TZ, Asia/Kolkata) for Indian leads. A different market needs its own zone.
- [ ] **Inbox placement before volume:** sending domain separate from `ozoo.ai`; SPF, DKIM and DMARC (`p=none` to start) pass on a test email (Gmail "Show original" shows PASS for all three); Google Postmaster Tools and Microsoft SNDS set up for the sending domain; warm-up done (the mailbox caps ramp 10 → 30 a day by themselves).
- [ ] **Test the real inboxes** listed in onboarding/03 section 6 (Gmail, Outlook.com, Microsoft 365 with Defender, iCloud, one address behind Proofpoint or Mimecast): untouched for an hour = zero `human` events; one real click + 20 s of reading = a `visit` event, verdict `human`.
- [ ] **Email copy (marketing):** each follow-up must make sense on its own (most leads see only one of the four: no "as I said in my last email"), end with a question that is easy to answer in one line, and match the Ozoo positioning (call QA, voice and chat agents for lenders). Today's `templates/emails.yaml` still says "automating outreach".
- [ ] **Access logs:** the production server and any proxy in front must not log request paths of `/r/`, `/o/`, `/u/` and `/v/` (they carry tokens). Locally `run_local` already passes `--no-access-log`.
- [ ] **Measure what counts:** report reply rate, verified visits and meetings booked per 100 leads. Do not report open rate.

Added 2026-11-18 (task 21):

- [ ] **Booking link and its webhook:** if `LINK_TARGETS.booking` is the Microsoft Bookings page (today's "book a demo" calendar on ozoo.ai), bookings there are **not** detected by the agent (no webhook): the lead keeps getting follow-ups until sales marks it. Either use Cal.com for the scoping call (the `/webhooks/cal` flow) or add a Microsoft Graph / Power Automate hook before volume.
- [ ] **Designed follow-ups:** send emails 2-4 to the test inboxes and check them in Gmail (web + phone), Outlook (web + desktop, images blocked by default) and Apple Mail: header, product image and button show, and the alt text reads well with images off. Check that they still reach Primary on the warmed-up domain; if not, remove the `rich:` block of step 2 first.
- [ ] **Image rights:** the screens in `static/email/` are ozoo.ai's own product screenshots with illustrative data; marketing confirms they may go in emails.
