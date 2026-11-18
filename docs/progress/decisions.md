# Decisions (rulings)

Source: the `Ruling:` lines in `.superpowers/sdd/2026-11-12-outreach-agent-v1/progress.md` and the `task-N-rulings.md` files. Each entry: **Decision**, **Why**, **Cost if wrong**. Where a source states no cost, the cost is our reading and marked "(inferred)".

## Project-wide

| Decision | Why | Cost if wrong |
|---|---|---|
| Task 1 spike uses public API docs, not trial accounts. Fixtures come from documented examples and carry a `_source` marker. | No accounts or credentials exist for agents. | Adapter field mappings need fixing after the first real webhook. |
| `execute()` calls channel, bot and switch actions through a `Deps` object of Protocols. Tests use fakes. | Task 6 must run actions whose real code arrives in tasks 8, 10, 12. | Small rewiring in Task 12. |
| `POST /intake` requires an `X-API-Key` header, compared in constant time. | The spec is silent. An open intake is a production hole. | Lead sources must send one header. |
| Production hardening everywhere: constant-time signature checks, 10 s timeouts (Claude 20 s), bounded retries on 429/5xx, no PII or secrets in logs, 1 MB webhook body cap, redirects only to configured targets. | Owner asked for production-ready and secure. | A little extra code. |
| Code must run on Python 3.10: use `class X(str, Enum)`, not `StrEnum`. | Local Python is 3.10.12 (Docker uses 3.12). | Rewrite of enums if we move to newer Python only. (inferred) |
| Task 13 ships Dockerfile, runbook, e2e checklist and a simulated e2e test. The real-inbox run is left to the user. | Needs real accounts and DNS that agents lack. | Real-world gaps appear only at the user's first live run. (inferred) |

## Task 1

No task-specific rulings beyond the project-wide spike ruling. Deferred minors are in [task-01](tasks/task-01-skeleton-spike.md).

## Task 3: intake

| Decision | Why | Cost if wrong |
|---|---|---|
| Payload is a pydantic model with length caps (254 email, 200 others). Unknown keys are ignored. Validation failure returns "rejected" and never raises. | Intake must never crash on bad input. | Rejected leads need a re-send. (inferred) |
| ~~Email is usable only with a valid syntax, `consent_email`, and verify status valid / catch_all / unknown.~~ **Superseded 2026-11-13, see Review update.** WhatsApp is usable with a valid E.164 phone and `consent_whatsapp`. | Consent rules for each channel. | A lead could be messaged without consent. (inferred) |
| Any contact on the suppression list gives `skipped_suppressed`, checked before dedup. Dedup by lowercased email or phone. | Opted-out people must never re-enter. | A suppressed person could get messaged. (inferred) |
| Account hold: a new lead whose same-domain colleague is Engaged, Qualifying or Booked is created, then moved to PAUSED (reason "account"). Free-mail domains are exempt. | Do not pitch a company already in conversation. | Colleague gets a cold email mid-deal. (inferred) |
| Accepted leads go INTAKE then IN_SEQUENCE with the chosen channel and score 0. Rejected leads are not stored. | One clean starting point. | None stated. |
| **Upgraded from minor to Important:** the unique-constraint race (two parallel intakes) is handled in a savepoint and maps to duplicate. | It would break the "intake never raises" promise in production. | A few lines of savepoint code. |
| `intake_lead` flushes but does not commit. ZeroBounce: 10 s timeout, any error gives "unknown", no email or key in logs. | Caller owns the transaction; verifier outage must not block intake. | None stated. |

## Task 4: links, bot filter, score

| Decision | Why | Cost if wrong |
|---|---|---|
| Link token is signed (HMAC), constant-time compared, never raises on garbage. Unknown tag or channel raises on creation. | Stops forged or tampered links. | None stated. |
| Bot rules in fixed order: WhatsApp click by user agent, scanner UA/IP, within 60 s of send, 2+ different tags within 10 s, then Apple proxy open as `machine`. | Scanners pre-click and pre-open mail. | Real humans mislabelled as bots (lost signal) or bots as humans (false hot alerts). (inferred) |
| Scanner list covers security vendors only. Apple proxy range is `17.0.0.0/8` seed. | Do not invent ranges. | Some scanners slip through until the list grows. (inferred) |
| Score: ~~opens +1 (max 3, +2 bonus at the 3rd)~~ opens 0 (review update), homepage 2, case study and product 5, pricing and booking 10, WhatsApp read +1 once. Halves after 30 days with no human event. Bands: cold 0-4, warm 5-14, hot 15+. | Spec section 5f. | Alert thresholds off. (inferred) |
| **Confirmed gap fixed:** `link_secret` (32+ chars) and `intake_api_key` (24+) must be strong; ten secrets must be non-blank. | Weak secrets would be accepted silently. | None stated. |

## Task 5: rules engine

| Decision | Why | Cost if wrong |
|---|---|---|
| **Unsubscribe link no longer opts out on GET.** Click shows a confirm page; only a POST (or the tool's one-click) creates an `unsubscribe` event. | Scanners pre-click every link and would suppress real prospects on both channels. | One extra click for a human unsubscribing by link. The tool's one-click header is unaffected. |
| Hard-bounce state effects (switch channel or SUPPRESSED) only from IN_SEQUENCE, PAUSED or NURTURE with email active. Other live states get only Suppress(email) plus ToolStop. `wa_failed` acts only if WhatsApp is active. | Late bounces must not kill booked or engaged leads. | Small. |
| A booking from a terminal-state lead raises Alert("booked", "booking from lead in terminal state"). | An explicit self-booking must reach sales. | An extra alert. |
| Rules use a frozen `LeadSnapshot` and `EventSnapshot` and stay pure (no DB, I/O or clock). | Easy to test, deterministic. | None stated. |
| Counters are emitted as `SetFlag` actions. The executor does not mutate them. | Keeps rules the single source of change. (inferred) | None stated. |
| Scoring uses pure `points_for`. Hot alert fires once when the band crosses to hot; a pricing click alerts unless already alerted. Never two hot alerts in one call. | Avoid alert spam. | A missed or doubled alert. (inferred) |
| ~~"New subject if not opened" is decided at render time~~ **Superseded 2026-11-13, see Review update.** Originally: decided at render time (Task 7) from `opened_steps`, not by a rule action. A `sent` event gives SetStepSent plus RefreshNextStep. Variants are emitted only for email-active leads. | Rules should not know template details. | None stated. |
| Messages go by active channel: email SendReply; WhatsApp text only if the 24 h window is open, else Alert("wa_human_needed"). | WhatsApp forbids free text outside the window. | A message that Meta rejects. (inferred) |
| Never emit SetState to the current state. Two-hop paths emit two SetStates in order (for example PAUSED to ENGAGED to BOOKED). | The state machine rejects no-op or skipped moves. | An illegal-transition alert. (inferred) |
| Replies, taps and texts stop all outreach and set ENGAGED. Button and booking handling follows the table in the ruling (BOOK_DEMO, ASK_QUESTION, no-show twice to NURTURE, meeting_ended to HANDED_OFF). | One channel at a time (spec B7). | Double messaging. (inferred) |
| Opt-outs apply in any state before the terminal guard: unsubscribe, unsubscribe-tag click, STOP keywords, WhatsApp code 131050. Terminal leads still record the suppression. | Opt-out always wins. | A person opted out keeps getting messages. (inferred) |
| Terminal leads ignore everything else. Non-human open, click or read events are ignored. | Review focus 3 and bot filtering. | None stated. |
| `wa_failed` handling by code: 131026 and 131049 switch to email if usable and not yet switched, else suppress or nurture; 131047 only logged; other codes alert. | Codes are from memory and unverified. | Wrong routing until real codes are recorded. |

## Task 6: adapter, webhooks, executor

| Decision | Why | Cost if wrong |
|---|---|---|
| **Instantly per-lead pause/resume is left unimplemented.** It raises and becomes an `integration_error` alert asking for a manual pause. | Guessing an endpoint could silently do nothing. | Manual pauses in the Instantly UI until verified. |
| `unmatched_reply` and `unmatched_inbound` alerts may include the sender address in Slack and Alert rows (never in logs). | Humans need it to act. | PII in internal Slack. |
| **Slack post moved to after the DB commit** (upgraded from minor). | A Slack outage held webhook transactions for about 31 s and double-posted on retry. | Needs a worker retry of unposted alerts (Task 12). |
| `http.request_with_retry`: retry on transport errors and 429/500/502/503/504; honour Retry-After (capped 10 s); 10 s client timeout. | Resilient outbound calls. | None stated. |
| Executor rules: illegal transitions become `integration_error` alerts, never crashes. Every external call is isolated: a failure is logged without PII, recorded in the DB, and the rest of the actions continue. Suppress is idempotent. | One bad port must not drop the other actions. | None stated. |
| Ingest owns commits. Duplicate provider event ids are skipped. Errors roll back, log and re-raise so the provider retries. Match order: tool lead id, email, phone, then domain (replies only). Clicks are stored unprocessed for the worker. | Safe retries, correct matching. | Wrong lead matched by domain. (inferred) |
| Email adapter targets Instantly using a shared-secret header, constant-time compare. Each unconfirmed field is marked `# UNVERIFIED:`. `reply()` returns False if unsupported. | No signature documented. | Webhook auth must change if Instantly offers signing. (inferred) |
| App routes, size caps and error bodies: 401 for bad secret, 413 over 1 MiB, never echo exception text, redirect always happens even if the DB fails. | Security and tracked links must not break. | None stated. |
| Unsubscribe is scanner-safe: GET `/r` logs and redirects to `/u/{token}`; POST `/u/{token}` creates the opt-out. Idempotent; accepts RFC 8058 one-click. | Same as the Task 5 ruling. | One extra click. |
| `Suppress(email)` or `Suppress(phone)` also marks the channel unusable on the lead (`verify_status="invalid"` or `consent_whatsapp=False`). | Fallback must never pick a dead channel. | A switch to a dead channel. |
| Carry-forward (Task 9): a domain-matched `unsubscribe` suppresses only the sender address. | Do not suppress the original prospect for a colleague's reply. | A prospect wrongly opted out. (inferred) |
| Carry-forward (Task 12): worker retries unposted Slack alerts and posts a digest of `integration_error`. | Follows the Slack-after-commit ruling. | Missed alerts. |

## Task 7: sequencer (rulings given, not yet built)

| Decision | Why | Cost if wrong |
|---|---|---|
| Template file layout and placeholders are fixed. Each body and reply contains `{booking_link}` and `{unsubscribe_link}`. Load-time validation names any missing key. | Every email must have both links (spec). | A broken template fails loudly at start-up. |
| Inserted values (first name, company) have CR/LF/tab removed, are stripped and capped at 80 chars. Inserted values are never re-formatted. | Stops email header injection through the subject. | Header injection. (inferred) |
| `enroll` is idempotent, uses the campaign for the lead's timezone group and raises before any tool call if the group is missing. | Avoid double enrolment and half-state. | A lead stuck without a campaign. (inferred) |
| ~~`end_of_sequence_route` branch order: hot, never-opened with WhatsApp available, clicked, opened, silent.~~ **Superseded 2026-11-13, see Review update.** Was: The caller stores the track. | Spec 5g and 5h. | Wrong follow-up path. (inferred) |
| No template text may contain "opened", "clicked", "read your" or "saw you". | Never tell prospects we track them (spec 5d). | Creepy messaging. (inferred) |


## Review update (2026-11-13, plan v2.1)

The final plan was reviewed; five points came back. Owner chose: restart rule "depends on outcome", EU "off by default, switch on", scope "plan + code + docs".

| Decision | Why | Cost if wrong |
|---|---|---|
| Email is usable with valid syntax and verify status valid / catch_all / unknown, and no `email_hold`. `consent_email` is recorded but **not required**. Every email is verified at intake. | Review: cold B2B email doesn't need permission; only WhatsApp does. | Fewer rejected leads; must keep one-click unsubscribe in every email. |
| Email opens are logged (`human_opens`) and nothing else: 0 points, no `team_invite` / `new_subject` variants, no `opened_steps`, not used at end of sequence. Templates lost those variants. | Review: the plan itself says opens are unreliable. | None: clicks, replies and bookings carry the real signal. |
| `end_of_sequence_route(lead, human_clicks)`: hot → `sales_personal` + alert, clicks → `warm_2_3w`, else `monthly`. No `SwitchChannel("never_opened")`. | Review: WhatsApp opt-in leads already get WhatsApp on day 1. | None. |
| EU / EEA leads (`eu.is_eu`: country, phone region or email country domain) get `email_hold="eu_approval"` unless `EU_EMAIL_APPROVED=true`. Email-only EU leads → `PAUSED("eu_approval")`, outcome `held_eu`. EU leads with WhatsApp opt-in go WhatsApp; email is not a fallback while held. `holds.release_eu_holds` clears the holds once approved. | Review: EU email needs approval (GDPR). Conservative detection: any EU signal counts. | A non-EU lead with a `.de` address waits for approval. |
| Website tracking script stays off until approved. Nothing to switch: the script is not built (next iteration). | Review: tracking needs approval (cookie consent). | None today. |
| Paused colleagues restart per `holds.colleague_resume_at`: blocker still Engaged → 14 quiet days; Qualifying / Booked / HandedOff → stay paused; `not_now` → `nurture_until` (else +90 d); `sales_rejected` / `wrong_person` → now; not interested / unsubscribed / other closed or nurture → +90 d; no longer active → now. Waits for the latest of all active colleagues. `paused_by` is stored on the paused lead. | Review: the plan never said when paused colleagues restart. | A colleague restarts too early (annoys the account) or never (lost lead). |
| A colleague in HANDED_OFF now also holds new intake leads at the same company. | Consistent with "sales owns the account". | New leads at a sales-owned account wait for sales. |
| `holds.py` is called by the Task 12 tick; it does not run on its own yet. | Worker not built. | Holds don't release until Task 12 ships. |

## Task 10b: Microsoft Teams instead of Slack

Decided on 2026-11-13: the office uses Microsoft Teams, not Slack. Slack is replaced everywhere.

| Decision | Why | Cost if wrong |
|---|---|---|
| Alerts post to a Teams **Workflows** webhook as an Adaptive Card. 2xx counts as posted. The flow is unchanged: store the Alert row, post after commit, the worker retries unposted rows. | Office 365 connector webhooks are retired. | Alerts missing if the flow owner leaves the team (go-live item). |
| Sales commands use a Teams **outgoing webhook**: `@OzooAgent wa <lead_id> <text>` and `@OzooAgent done <lead_id>`. It is verified with HMAC-SHA256 of the raw body using the base64 security token, compared in constant time. | The Teams equivalent of Slack slash commands. | n/a |
| Extra checks beyond the HMAC: the signed activity `timestamp` must be within ±5 minutes, a replayed activity `id` is dropped, and tenant, sales channel and sender (`aadObjectId`) must be on allow-lists. | The outgoing webhook HMAC has no timestamp of its own and works in every channel of the team. | Without these checks, a replayed or wrong-channel command could message a lead. |
| The command is answered immediately (the 5 s limit) and the WhatsApp send runs in the background. | Teams times out at 5 seconds. This pulls a Task 12a item forward. | n/a |
| Lead text in Teams cards is neutralised: markdown characters are swapped for safe lookalikes, URLs are defanged, and the text goes in its own TextBlock. | Adaptive Card markdown has no reliable escaping, and Teams auto-links URLs. | Lead text reads slightly oddly. |
| Config: `TEAMS_WEBHOOK_URL` (secret, https), `TEAMS_OUTGOING_SECRET` (base64, non-blank), `TEAMS_TENANT_ID`, `TEAMS_SALES_CHANNEL_ID`, `TEAMS_ALLOWED_AAD_IDS`. | n/a | n/a |
| Review fixes: `teams_escape` applies NFKC, defangs `www.`, bare IPv4, and lookalike dots; allow-list ids are stripped, tenant/AAD compared case-insensitively; deeply nested JSON → 400. | Security review of 10b. | n/a |
| Bot stays muted if a background `wa` send fails, until `done`; the failure is alerted. ChatMessage stored only after a successful send. | Avoid the bot talking over a human; no record of unsent messages. | Sales must type `done` after a failed send. |

## Task 11: Cal.com booking

| Decision | Why | Cost if wrong |
|---|---|---|
| Handoff happens as soon as the meeting ends. A no-show reported after the handoff only alerts sales. | Sales hosted the meeting and own the lead from then on. | A no-show lead sits with sales, but they are told. |
| An unmatched form with high or medium fit raises an alert. Low fit is stored silently. | A good lead is never invisible. | Some alert noise. |
| The Cal signature is accepted as bare hex or with a `sha256=` prefix. | The exact format is unverified. | n/a |
| A reschedule while QUALIFYING counts as a rebooking (back to BOOKED). | A lead who rebooks through Cal.com's own reschedule link must still be handed off. | n/a |

## Task 9b: pluggable AI model (2026-11-13)

| Decision | Why | Cost if wrong |
|---|---|---|
| Reply classification and the chatbot use one `LLMClient` port. The provider is set in config with `LLM_PROVIDER` = `anthropic`, `openai`, `azure_openai` or `openai_compatible` (Gemini, Mistral, Groq, OpenRouter, Ollama, vLLM). Models are set separately for the classifier and the chat. | The company decides which model to use. | n/a |
| The safety checks stay outside the model: any model error goes to unsure / a human, and the price, link and referral checks and prompt-injection handling stay unchanged. | The same safety holds whatever the provider. | n/a |

## Final review (2026-11-13)

Verdict: safe for local testing. Real sending is blocked until Instantly is verified on a trial account.

| Decision | Why | Cost if wrong |
|---|---|---|
| A failed stop at the email tool is retried (5 min, 30 min, 2 h, then every 6 h) and sends an immediate Teams alert. Any `sent` event for a lead that has left the sequence re-issues the stop. Opt-outs are also added to the tool's block list. End of sequence stops the tool lead. | Otherwise opted-out or replying leads keep getting cold emails. | Without it, emails go to opted-out people. |
| The lead row is locked and refreshed before ingest and before each worker action. An AI job re-checks the lead after the AI call and discards its result if the lead changed. | Stops a background job from undoing a booking, and stops a worker from acting on a stale copy. | n/a |
| A transient WhatsApp failure is retried with backoff. A permanent failure falls back to email, or to nurture. A failed email enrolment goes to nurture after 3 tries. A `stuck_leads` alert is the safety net. | No lead stays stuck forever. | n/a |
| Teams commands `retry`, `nurture`, `close` and `status` were added. | Sales need a way to recover leads in production. | n/a |
| Once-only sends: booking slots at most once per 7 days, no repeat booking link within 24 h, and an email "enrol attempted" marker with a lookup before re-adding. | Avoids repeat messages to prospects. | n/a |
| If the email verifier itself fails, the lead is held (`verify_error`) and retried. It is never sendable. | "Every email is verified." | n/a |
| Local mode with real email or WhatsApp refuses time speed-ups unless `ALLOW_REAL_LOCAL=1` is set. | Real prospects must never get the 48 h reminder within minutes. | n/a |
| Ops: `outreach` logging is configured, the access log is off locally, `X-Forwarded-For` is trusted only with `TRUST_PROXY_HEADERS=1`, and dependencies are pinned (`requirements.lock`). | Go-live hygiene. | n/a |
| Deferred to the Instantly trial account: per-lead and campaign pause, and the block, find and delete semantics. Database migrations are deferred to go-live. | Needs real accounts. | n/a |

## Task 18: clicks alone need a check (2026-11-18)

| Decision | Why | Cost if wrong |
|---|---|---|
| While a lead has no reply and no booking, clicks that would alert sales send a `click_check` alert instead of `hot_lead` / `hot_no_reply` (`CLICK_ALERTS_NEED_CHECK`, default on). Points, the booking-slots email and the nurture track are unchanged. | Bank security sandboxes can open links minutes after delivery and run JavaScript, so they can pass the click filter. A false hot lead wastes a sales call and looks bad to the prospect. | A real clicker gets a slightly slower call (someone checks first). Turn it off once real data shows the filter holds. |

## Task 19: real leads, real tracking (2026-11-18)

| Decision | Why | Cost if wrong |
|---|---|---|
| A **verified visit** (the site's `/v.js` reports 15+ s of active reading with 40 % scroll or 3+ interactions, or a second page with 8+ s) is a human signal: points once per page (homepage 3, case study / product 5, pricing 10), `hot_lead` to sales even while clicks need a check, and the `verified_visit` flag that lifts the check for that lead. The server re-checks the rule and the bot filter runs on it. | Scanners fetch pages; they don't read them. This is the strongest signal short of a reply. | A sandbox that scrolls and waits 15 s would pass: none seen so far; its IP still goes through the filter. |
| While clicks need a check (no reply, booking or verified visit), clicks alone send **no automatic email** to the lead: no booking-slots email after a pricing click, no booking-click nudge. The slots email goes after a verified pricing visit instead (still at most once per 7 days); a held booking-click nudge (`booking_nudge_held`) goes out on the lead's first verified visit of any page. Overrides the task-18 note "booking-slots email unchanged". | The simulation showed a smart bank sandbox making the agent email a person who never read anything. That looks automated and burns the domain. | A real clicker whose site visit isn't measured (no snippet) waits for sales' check instead of an instant email. |
| Tracked links to our own pages forward with `?oz_v=<signed token>` (`VISIT_TRACKING`, default on); the snippet removes it from the address bar. The token carries lead, step, page and channel, signed with `LINK_SECRET`. | No cookies or third-party tracker needed; the token only works for our beacon. | Turn off with `VISIT_TRACKING=false`. |
| Our own sender sends sequence emails only in the send window (Mon-Fri 09:30-17:30, `EMAIL_SEND_TZ` default BUSINESS_TZ). Email 1 at the first slot; steps 2/3/4 at 15:00 / 10:30 / 15:30 plus a per-lead 0-40 min offset. Replies to a person are never held. Off locally unless `EMAIL_SEND_WINDOW` is set; off whenever `TIME_SCALE` is not 1. | Most leads see one of four emails. Working-hours sends and varied times of day raise the odds the one they see is ours; evenly spaced bursts look like a bot. | Leads in other time zones need their own `EMAIL_SEND_TZ`. |
| `python -m outreach.world` is the acceptance check for tracking and rule changes: every story persona's outcome must stay as expected and "hot-lead alerts / wrong" must be 0. | Unit tests check rules one at a time; real leads mix machines, silence and late replies. | n/a |
| Visit tokens carry their issue time and expire 2 days after the click; duplicate beacons are keyed per lead, step, page, day **and visitor network**. | A token in a URL can leak (site logs, analytics); per-network keys stop a scanner's bot-marked beacon from swallowing the person's. | A person who reads the page 3 days after clicking isn't counted (they can click again). |

## Task 20: real email copy and automatic replies (2026-11-18)

| Decision | Why | Cost if wrong |
|---|---|---|
| Copy is written by people (templates/emails.yaml), not generated per email by the AI. Variety comes from a role-based angle per lead plus alternative subjects and bodies, picked by a stable hash of the lead. | Every sentence is checked against the KB before go-live; an AI-written cold email can invent a claim. Stable picks mean a lead never gets two versions of one email. | Less novelty than fully generated copy. An AI-written first line, checked like the chatbot, can be added later. |
| An emailed question is answered automatically when the KB covers it (chatbot guardrails and post-check: KB facts only, no links, no addresses, no unlisted prices). Otherwise the lead gets "a colleague will reply within one business day". Sales get the alert either way. | The lead hears back in seconds instead of waiting for a person; the risky part (the facts) stays inside the KB. | A KB answer that is technically right but not what they asked; sales see it in the alert. |
| Email courtesy replies: not now ("I'll check back in <month>"), not interested (one closing note), wrong person (thanks, or "who is the right person?"). None for out-of-office (an auto-reply) or an opt-out (no more email). Each reply is optional in the templates. | That's how a person would answer; silence after "not now" reads as automated. | One extra email to someone who declined; it promises no further follow-up. |
