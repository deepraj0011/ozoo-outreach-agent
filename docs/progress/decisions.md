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
| Email is usable only with a valid syntax, `consent_email`, and verify status valid / catch_all / unknown. WhatsApp is usable with a valid E.164 phone and `consent_whatsapp`. | Consent rules for each channel. | A lead could be messaged without consent. (inferred) |
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
| Score: opens +1 (max 3, +2 bonus at the 3rd), homepage 2, case study and product 5, pricing and booking 10, WhatsApp read +1 once. Halves after 30 days with no human event. Bands: cold 0-4, warm 5-14, hot 15+. | Spec section 5f. | Alert thresholds off. (inferred) |
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
| "New subject if not opened" is decided at render time (Task 7) from `opened_steps`, not by a rule action. A `sent` event gives SetStepSent plus RefreshNextStep. Variants are emitted only for email-active leads. | Rules should not know template details. | None stated. |
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
| `end_of_sequence_route` branch order: hot, never-opened with WhatsApp available, clicked, opened, silent. The caller stores the track. | Spec 5g and 5h. | Wrong follow-up path. (inferred) |
| No template text may contain "opened", "clicked", "read your" or "saw you". | Never tell prospects we track them (spec 5d). | Creepy messaging. (inferred) |
