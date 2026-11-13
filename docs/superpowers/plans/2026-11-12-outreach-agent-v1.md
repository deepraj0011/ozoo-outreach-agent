# Ozoo.ai Outreach Agent v1 (Email + WhatsApp) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One outreach agent that takes any lead (email only, WhatsApp only, or both) through an intake gate and runs the right channel. Email is a 4-email sequence through a cold-email tool. WhatsApp is an opt-in template with 2 buttons, a reminder and a chatbot. The agent tracks every event, filters bots, scores engagement, classifies replies with Claude, books through Cal.com and alerts sales in Microsoft Teams. Both channels run on **one lead record and one state**.

**Architecture:** One Python service (FastAPI) owns the lead state machine in a SQL database. External tools (cold-email tool, WhatsApp Cloud API, Cal.com) only *send* and *report*. Every incoming signal from any channel is stored as an `Event`. A pure rules function `decide(lead, event) -> list[Action]` turns events into actions, and one executor applies them. A channel policy decides which channel is active for each lead; only one channel sends at a time. A 60-second `tick()` loop handles time: settled clicks, WhatsApp reminders, channel fallbacks, OOO resumes, end-of-sequence routing, score decay and SLA escalations.

**Tech Stack:** Python ≥ 3.10 · FastAPI + uvicorn · SQLAlchemy 2.x (SQLite locally, Postgres in prod via `DATABASE_URL`) · httpx · `anthropic` SDK · `phonenumbers` · pydantic-settings · PyYAML · pytest + respx · Meta WhatsApp Cloud API (Graph API) · Docker for deploy (Render / Railway).

**Spec:** `docs/Outreach_Agent_Plan_v2.md` (approved). Section numbers (§4, §5i, §7 …) refer to it. **Scope change vs the spec's §18:** the WhatsApp flow + chatbot move from "next iterations" into v1, at the user's request on 2026-11-12.

> **Review update 2026-11-13 (plan v2.1).** Overrides anything below that conflicts:
> 1. Email needs **no opt-in** (`consent_email` is recorded, not required); WhatsApp still needs opt-in. Every email is verified at intake.
> 2. Email opens are **logged only** (`human_opens` counter). No points, no `team_invite` / `new_subject` variants, no effect on end-of-sequence routing.
> 3. No email → WhatsApp switch at end of sequence (`end_of_sequence_route(lead, human_clicks)`: hot → `sales_personal`, clicks → `warm_2_3w`, else `monthly`). The hard-bounce switch and the WhatsApp → email fallback stay.
> 4. EU leads (`eu.is_eu`): email held (`email_hold="eu_approval"`) until `EU_EMAIL_APPROVED=true`. EU email-only leads → `PAUSED("eu_approval")`, intake outcome `held_eu`. Website tracking script stays off until approved.
> 5. Paused colleagues restart per `holds.colleague_resume_at` (table in spec §11). Task 12's tick must call `holds.resume_account_holds` and, when `EU_EMAIL_APPROVED` is on, `holds.release_eu_holds`. Task 9 must use the state-change reasons `not_now` (with `nurture_until`), `not_interested`, `wrong_person`; Task 11 / CRM uses `sales_rejected` and `low_fit`.
> 6. Task 10b (2026-11-13): Slack replaced by Microsoft Teams — see docs/progress/decisions.md

## Global Constraints

- Scope: post-intake only. No lead sourcing. **Still out of v1:** nurture *sending*, CRM sync, website tracking script, dashboard, AI voice call.
- Every lead has exactly one `LeadState` at a time, across both channels (§11). Every state change goes through `states.transition()`.
- **Channel policy** (`channels.choose_channel`):

  | Lead has | Starts on | Fallback |
  |---|---|---|
  | Email only (verified / catch-all) | Email | — |
  | Phone + WhatsApp opt-in, no usable email | WhatsApp | — |
  | Both, with WhatsApp opt-in | `PRIMARY_WHEN_BOTH` (default `whatsapp`, per §7 / spec's ❓ "Yes" path) | The other channel |
  | Phone without WhatsApp opt-in + email | Email | — (phone is never messaged) |
  | Phone without opt-in, no email | Rejected | — |

- **Only one channel sends at a time** (§3 bottleneck B7). Any reply, button tap or booking on *either* channel stops outgoing messages on *both*. An opt-out on either channel suppresses **email and phone** (§7, §5i).
- **Channel switch triggers.** WhatsApp → email when the send fails (not on WhatsApp, marketing limit) or when there is no tap/reply 48 h after the reminder. Email → WhatsApp (if opt-in) on a hard bounce (§5c) only. A lead switches channel **at most once**; after that it goes to `NURTURE`.
- Email sequence: Email 1 Day 0 · Email 2 Day 4 · Email 3 Day 9 · Email 4 Day 15, then 5 days before end-of-sequence routing (§5g, §5h).
- WhatsApp: first message = approved template `ozoo_intro` with exactly 2 quick-reply buttons, payloads `BOOK_DEMO` and `ASK_QUESTION` (§7). One reminder template `ozoo_reminder` 48 h later if no tap/reply. Free-form messages only while `wa_window_until > now` (24 h after the lead's last inbound message). Everything else is template-only.
- Only events with `verdict == "human"` change the flow. Bot and machine events are stored, never acted on (§5b). WhatsApp `read` receipts are hints only, like opens.
- Bot filter: within **60 s** of send → bot. ≥ 2 different link tags from the same message within **10 s** → all bot. Known scanner UA/IP → bot. Apple Mail Privacy Protection open → `machine`.
- Score points (§5f): open 0 (logged only) · homepage +2 · case_study / product +5 · pricing +10 · booking click not booked +10 · WhatsApp read +1 (max 1). Score halves after 30 days with no human event. Bands: 0–4 `cold` · 5–14 `warm` · ≥ 15 `hot`.
- Mailbox health (§5j): bounce rate **> 2 %** over 7 days, with at least 50 sends → pause that campaign and alert.
- Soft bounce: 3 soft bounces → treat as hard bounce (§5c).
- Never write "I saw you opened/clicked/read" in any message (§5d).
- Every email has a booking link + one-click unsubscribe. All links go through our redirect with a purpose tag. The cold-email tool's own link tracking is **OFF**. Booking links sent on WhatsApp go through the same redirect.
- Link purpose tags: `booking`, `pricing`, `case_study`, `product`, `homepage`, `unsubscribe`.
- Reply classes (§6), used for email replies and WhatsApp free text: `interested`, `question`, `not_now`, `not_interested`, `unsubscribe`, `ooo`, `wrong_person`, `unsure`.
- WhatsApp opt-out keywords (case-insensitive, whole message trimmed): `stop`, `unsubscribe`, `opt out`, `optout`, `remove me`. They are handled before any LLM call.
- Chatbot guardrails (§7): never quote custom prices · never promise features or timelines · answer only from `kb/ozoo_kb.md` · hand off to a human when unsure · log every message. At most **6 bot replies** per conversation, then it offers a demo booking or a human.
- Human WhatsApp SLA: **1 business hour** (§7). Business hours come from `BUSINESS_TZ` / `BUSINESS_HOURS` (default Mon–Fri 09:00–18:00).
- Claude models: `CLASSIFIER_MODEL` and `CHATBOT_MODEL`, both default `claude-sonnet-5-5`.
- Never cold-email from the `ozoo.ai` root domain. Secrets come only from env vars and are never committed.

## Spec deviations in v1 (tell the lead)

These are confirmed or removed by the Task 1 spike.

1. **Email send time is per campaign, not per lead.** We use one campaign per timezone group. Only the subject-line change is per lead.
2. **"Pull next email forward" for hot email leads** happens only if the tool supports replying in the thread (C4). Otherwise it becomes a Teams alert asking sales to email personally.
3. **"Question" replies by email** get a human-reviewed, Claude-drafted answer through Teams. They are not auto-sent: email is async and the risk is higher. WhatsApp questions *are* answered by the chatbot, because that channel is real-time.
4. **WhatsApp human takeover happens in Microsoft Teams.** Sales reads the transcript in the Teams sales channel and replies by mentioning the outreach bot with `wa <lead_id> <message>`. There is no shared WhatsApp inbox in v1.
5. **Apple Mail Privacy Protection detection** works only if the tool's open webhook includes the IP or user-agent.
6. **Spam rate (Google Postmaster)** is checked manually each day. Only the bounce-rate check is automated.
7. **WhatsApp goes live only after Meta business verification + template approval.** Until then, everything is built and tested on Meta's free **test number** (up to 5 registered recipient phones).

## Review Focus

1. **Lead with both channels gets double-messaged, or one channel keeps going after a reply on the other** (§3 B7). A WhatsApp tap must stop the email sequence, and an email reply must cancel the pending WhatsApp reminder. Pinned in Task 5 and Task 12.
2. **Duplicate or out-of-order webhooks** from the email tool, Meta or Cal.com (all of them retry). The same provider event id must act once. Pinned in Tasks 6, 8 and 11.
3. **Events for a lead in a terminal state** (`Suppressed`, `Closed`, `HandedOff`, `Rejected`, `Archived`). A click or WhatsApp tap after opting out must not alert or score, though a new opt-out is still recorded. Pinned in Task 5.
4. **Claude API failure** (timeout, 5xx, malformed output, confidence < 0.7) in the classifier or chatbot → `unsure` / human handoff. The message is never lost and the prospect is never left without an answer on WhatsApp. Pinned in Tasks 9 and 10.
5. **Sender that doesn't match a lead.** An email reply from a colleague's address → matched by domain (account rule). A Cal.com booking with a personal email → matched by the metadata token. A WhatsApp message from an unknown number → `unmatched_inbound` alert, never auto-answered. Pinned in Tasks 6, 8 and 11.

## If tomorrow runs short — cut line

Tasks 1–9 and 11–13 are the core. **Task 10 (chatbot) is the one to cut.** Without it, an `ASK_QUESTION` tap goes straight to human takeover (Task 10's Teams part is small; build that part first). This keeps the full WhatsApp flow working, just with no bot.

---

## File Structure

```
pyproject.toml                 deps, pytest config
.env.example                   every env var, no values
Dockerfile
templates/emails.yaml          email subject/body per step × variant (copy owned by marketing)
templates/whatsapp.yaml        names + variables of approved Meta templates, free-form texts
kb/ozoo_kb.md                  chatbot knowledge base (owned by marketing)
config/scanners.yaml           security-scanner UA substrings + IP CIDRs
src/outreach/
  config.py                    Settings
  db.py                        engine, SessionLocal, Base, init_db()
  models.py                    Lead, Event, Suppression, Alert, ChatMessage
  states.py                    LeadState, ALLOWED, TERMINAL, transition()
  channels.py                  choose_channel(), fallback_channel()
  intake.py                    intake_lead()
  verify.py                    EmailVerifier (ZeroBounce), normalize_phone()
  links.py                     make_link(), parse_token()
  botfilter.py                 classify_event()
  score.py                     apply_points(), decay(), band()
  actions.py                   Action dataclasses + execute()
  rules.py                     decide()  ← pure, both channels
  emailtool/base.py            EmailTool protocol
  emailtool/<tool>.py          Instantly or Smartlead adapter (chosen in Task 1)
  emailtool/fake.py
  whatsapp/client.py           WhatsAppClient (Cloud API) + FakeWhatsApp
  whatsapp/webhook.py          parse + verify Meta webhooks → NormalizedEvent
  sequencer.py                 email enroll/variants/end-of-sequence
  outreach.py                  start_outreach(), switch_channel()
  classifier.py                classify_reply()
  chatbot.py                   bot_reply()
  handoff.py                   takeover, SLA (Teams wa / done commands live in app.py)
  booking.py                   Cal.com handling, fit_from_answers()
  alerts.py                    TeamsAlerter (Adaptive Card to a Teams Workflows webhook)
  health.py                    check_mailboxes()
  worker.py                    tick()
  app.py                       FastAPI routes
tests/  (one file per module + tests/fixtures/*.json captured in Task 1)
```

---

### Task 1: Tool spike + project skeleton

This is the gate for the email tool choice and the WhatsApp setup.

**Files:**
- Create: `docs/tool-capabilities.md`, `tests/fixtures/*.json`, `pyproject.toml`, `.env.example`, `.gitignore`, `src/outreach/__init__.py`, `src/outreach/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` with these fields, and `get_settings()` (cached):
  - Core: `database_url`, `link_secret`, `link_base_url`, `link_targets: dict[str,str]`
  - Email tool: `tool_name ("instantly"|"smartlead")`, `tool_api_key`, `tool_webhook_secret`, `campaign_ids: dict[str,str]`, `default_tz_group`
  - Claude: `anthropic_api_key`, `classifier_model`, `chatbot_model`
  - Intake: `zerobounce_api_key`
  - Cal.com: `cal_webhook_secret`, `cal_booking_url`
  - Microsoft Teams: `teams_webhook_url`, `teams_outgoing_secret`, `teams_tenant_id`, `teams_sales_channel_id`, `teams_allowed_aad_ids`
  - WhatsApp: `wa_phone_number_id`, `wa_access_token`, `wa_app_secret`, `wa_verify_token`, `wa_graph_version` (e.g. `"v21.0"`)
  - Channels and hours: `primary_when_both ("whatsapp"|"email")`, `business_tz`, `business_hours`

- [ ] **Step 1: `git init`, then fill in `docs/tool-capabilities.md` with yes/no + the endpoint for each capability.**
  - **Email tool** (trial account):
    - C1: add a lead with custom variables
    - C2: update variables mid-sequence
    - C3: pause / resume / remove one lead
    - C4: reply in thread
    - C5: webhooks for sent (with step), reply (with body + thread/lead id), bounce hard/soft, unsubscribe and open (IP/UA?), with signing
    - C6: custom tracking domain + link tracking can be turned off
  - **Cal.com**:
    - K1: routing form → event types
    - K2: webhooks BOOKING_CREATED, BOOKING_CANCELLED, BOOKING_NO_SHOW_UPDATED, MEETING_ENDED and FORM_SUBMITTED, with the `X-Cal-Signature-256` header
    - K3: `metadata[lead]=` comes back in the payload
  - **WhatsApp Cloud API** (Meta developer app, test number):
    - W1: send `hello_world` to a registered test recipient
    - W2: create `ozoo_intro` (2 quick-reply buttons) and `ozoo_reminder` and **submit them for approval today**
    - W3: webhook verification (`hub.challenge`) + `X-Hub-Signature-256` signing
    - W4: record the actual error codes for "recipient not on WhatsApp" (expected 131026), "marketing limit" (131049), "outside 24 h window" (131047) and "user stopped marketing" (131050)
    - W5: the button-tap payload shape

  Mandatory: C1, C3, C5, C6, K2, W1, W3, W5. If an email mandatory is missing, switch between Instantly and Smartlead.
- [ ] **Step 2: Save one real payload per webhook type.**
  - Email tool: `tool_sent`, `tool_reply`, `tool_bounce_hard`, `tool_bounce_soft`, `tool_unsub`, `tool_open`
  - Cal.com: `cal_booking_created`, `cal_no_show`, `cal_meeting_ended`, `cal_form_submitted`
  - WhatsApp: `wa_status_sent`, `wa_status_delivered`, `wa_status_read`, `wa_status_failed_131026`, `wa_button_book`, `wa_button_ask`, `wa_text`
- [ ] **Step 3: Write `tests/test_config.py::test_settings_load_from_env`.** Check the defaults: both models are `claude-sonnet-5-5` and `primary_when_both == "whatsapp"`; also check that the `link_targets` JSON round-trips.
- [ ] **Step 4: Implement `config.py`.** Add the deps to `pyproject.toml` (+ `phonenumbers`). Run `pip install -e .[dev] && pytest -q`. Expected: 1 passed.
- [ ] **Step 5: Commit** `chore: skeleton, tool/Cal.com/WhatsApp capability spike, webhook fixtures`.

---

### Task 2: Data model + state machine

**Files:**
- Create: `src/outreach/db.py`, `models.py`, `states.py`, `tests/conftest.py`, `tests/test_states.py`

**Interfaces:**
- Produces:
  - `LeadState(str, Enum)`: `INTAKE, REJECTED, SUPPRESSED, IN_SEQUENCE, PAUSED, ENGAGED, QUALIFYING, BOOKED, HANDED_OFF, NURTURE, CLOSED, ARCHIVED`
  - `TERMINAL = {REJECTED, SUPPRESSED, CLOSED, HANDED_OFF, ARCHIVED}`
  - `transition(session, lead, to, reason, now) -> None`. Raises `IllegalTransition`; writes `Event(kind="state_change")`.
  - `Lead` columns:
    - Identity: `id, email (unique nullable, lowercased), phone (unique nullable, E.164), name, company, domain, role, source`
    - Consent: `consent_email, consent_whatsapp, consent_at`
    - Profile: `industry, size, timezone_group, verify_status`
    - State: `state, pause_reason, pause_until, score, last_human_event_at`
    - Channels: **`active_channel ("email"|"whatsapp"|None)`, `channel_switched: bool`**
    - Email: `step_sent, last_sent_at, tool_lead_id`
    - WhatsApp: **`wa_template_sent_at, wa_reminder_sent_at, wa_window_until, wa_bot_turns: int, wa_human_owner: str|None`**
    - Other: `variant_flags: JSON, nurture_track, nurture_until, created_at`
  - `Event` columns: `id, lead_id (nullable), channel ("email"|"whatsapp"|"web"|"cal"|"system"), kind, provider_event_id (unique nullable), occurred_at, received_at, step, tag, ip, user_agent, verdict, processed, data: JSON`
  - `ChatMessage(id, lead_id, direction "in"|"out", author "lead"|"bot"|"human", text, wa_message_id, created_at)`
  - `Suppression(value unique, kind "email"|"phone", reason, created_at)`, `Alert(id, lead_id, kind, text, created_at, resolved_at)`
  - Fixtures: `session` (in-memory SQLite) and `make_lead(session, **kw)`

- [ ] **Step 1: Write the tests.**
  - `test_allowed_path` (`IN_SEQUENCE→ENGAGED→QUALIFYING→BOOKED→HANDED_OFF`, 4 state_change events)
  - `test_illegal_transition_raises` (`INTAKE→BOOKED`)
  - `test_any_non_terminal_can_be_suppressed`
  - `test_terminal_states_are_final`
  - `test_lead_needs_email_or_phone` (both null → DB check constraint error)
- [ ] **Step 2: Run the tests.** Expected: FAIL.
- [ ] **Step 3: Implement.** `ALLOWED` is the §11 diagram plus:
  - `INTAKE→PAUSED` (account hold)
  - `PAUSED→ENGAGED` and `ENGAGED→PAUSED` (OOO reply)
  - `PAUSED→SUPPRESSED`
  - `IN_SEQUENCE→BOOKED`, `NURTURE→BOOKED`, `ENGAGED→BOOKED`
  - `IN_SEQUENCE→QUALIFYING` (WhatsApp `BOOK_DEMO` tap)
  - `IN_SEQUENCE→CLOSED` (unreachable on WhatsApp, no email)
  - `BOOKED→NURTURE`, `NURTURE→QUALIFYING`
  - `IN_SEQUENCE→IN_SEQUENCE` is **not** a transition. A channel switch changes `active_channel` only.
- [ ] **Step 4: Run the tests.** Expected: 5 passed.
- [ ] **Step 5: Commit** `feat: lead model (both channels) and single-state machine`.

---

### Task 3: Intake gate + channel choice (§4)

**Files:**
- Create: `src/outreach/verify.py`, `channels.py`, `intake.py`, `tests/test_intake.py`, `tests/test_channels.py`

**Interfaces:**
- Produces:
  - `EmailVerifier.check(email) -> "valid"|"invalid"|"catch_all"|"unknown"` (any error → `"unknown"`)
  - `normalize_phone(raw: str, default_region: str|None) -> str|None` (E.164 via `phonenumbers`; invalid → None)
  - `choose_channel(lead, primary_when_both) -> "email"|"whatsapp"|None` (follows the Global Constraints table)
  - `fallback_channel(lead) -> "email"|"whatsapp"|None` (the other usable channel if `not channel_switched`, else None)
  - `IntakeResult(lead_id, outcome: "accepted"|"rejected"|"skipped_duplicate"|"skipped_suppressed"|"held_account", channel: str|None, reason)`
  - `intake_lead(session, payload, verifier, now) -> IntakeResult`

- [ ] **Step 1: Write the tests.**
  - channels: one test per row of the channel-policy table, plus `test_primary_when_both_email_flag` and `test_fallback_none_after_switch`.
  - intake:
    - `test_missing_required_field_rejected` (required: name, company, role, source, and email or phone)
    - `test_invalid_email_no_phone_rejected`
    - `test_invalid_email_with_wa_phone_goes_whatsapp`
    - `test_catch_all_accepted`
    - `test_duplicate_email_skipped` (case-insensitive)
    - `test_duplicate_phone_skipped` (`+91 98xxx` vs `098xxx` normalise to the same value)
    - `test_suppressed_email_skipped`, `test_suppressed_phone_skipped`
    - `test_colleague_engaged_held` (only `ENGAGED`/`QUALIFYING`/`BOOKED` colleagues hold)
    - `test_free_mail_domain_not_used_for_account_rule` (list in `intake.py`: gmail.com, googlemail.com, outlook.com, hotmail.com, yahoo.com, icloud.com, proton.me)
    - `test_no_usable_channel_rejected`
- [ ] **Step 2: Run the tests.** Expected: FAIL.
- [ ] **Step 3: Implement.** Accepted → `IN_SEQUENCE` with `active_channel = choose_channel(...)`. Sending starts in Task 12 (`start_outreach`). Intake doesn't send.
- [ ] **Step 4: Run the tests.** Expected: pass. Add `test_zerobounce_error_returns_unknown` (respx 500) and check it passes.
- [ ] **Step 5: Commit** `feat: intake gate with channel policy, phone normalisation, dedup, suppression`.

---

### Task 4: Tracked links + bot filter + score (§5b, §5e, §5f)

**Files:**
- Create: `src/outreach/links.py`, `botfilter.py`, `score.py`, `config/scanners.yaml`, tests for each

**Interfaces:**
- Produces:
  - `make_link(lead_id, step, tag, channel="email") -> str` → `f"{link_base_url}/r/{token}"`. The token is base64url `lead_id.step.tag.channel` + a 16-char HMAC-SHA256.
  - `parse_token(token) -> tuple[int,int,str,str] | None`
  - `classify_event(event, sent_at, sibling_clicks, scanners) -> "human"|"bot"|"machine"`
  - `apply_points(lead, event) -> int`, `decay(lead, now) -> bool`, `band(score) -> "cold"|"warm"|"hot"`

- [ ] **Step 1: Write the tests.**
  - links: round-trip, tampered → None, unknown tag → None.
  - botfilter:
    - `test_click_within_60s_of_send_is_bot` (59 s → bot, 61 s → human)
    - `test_two_tags_within_10s_all_bot`
    - `test_same_tag_twice_is_human`
    - `test_scanner_user_agent_is_bot`
    - `test_scanner_ip_cidr_is_bot`
    - `test_apple_proxy_open_is_machine`
    - `test_open_without_ip_or_ua_is_human_hint`
    - `test_whatsapp_link_click_skips_60s_rule`: WhatsApp link previews are not security scanners. A WhatsApp click is a bot only if the UA is `WhatsApp/…` (link-preview fetcher).
  - score:
    - `test_open_points_cap` (+1,+1,+3,0,0)
    - `test_tag_points`
    - `test_wa_read_counts_once`
    - `test_decay_halves_after_30_days` (15 → 7), `test_no_decay_at_29_days`
    - `test_bands` (4/5/14/15)
- [ ] **Step 2: Run the tests.** Expected: FAIL.
- [ ] **Step 3: Implement.** Store `last_decay_at` in `variant_flags`.
- [ ] **Step 4: Run the tests.** Expected: pass.
- [ ] **Step 5: Commit** `feat: signed tracked links, bot filter, engagement score`.

---

### Task 5: Rules engine — both channels (§5d, §5e, §5i, §7)

This is a pure function with no I/O. Every row of §5i and every branch of §7 is a unit test.

**Files:**
- Create: `src/outreach/actions.py` (dataclasses only here), `rules.py`, `tests/test_rules.py`

**Interfaces:**
- Produces these frozen dataclasses:
  - State and suppression: `SetState(to, reason)`, `Suppress(value, kind, reason)`, `SuppressAllChannels(reason)` (both email and phone)
  - Email tool: `ToolStop()`, `ToolPause(until)`, `ToolResume()`
  - WhatsApp sends: `WaCancelReminder()`, `WaSendText(text_key: str)` (free-form, only valid inside the window), `WaSendTemplate(name: str)`
  - Routing: `SwitchChannel(reason)`, `SetVariant(flag)`, `AddScore(points)`
  - `Alert(kind, text)`, where kind ∈ `{hot_lead, hot_no_reply, question, unsure, unmatched_reply, unmatched_inbound, unmatched_booking, mailbox_paused, booked, no_show, wa_human_needed, wa_sla_missed}`
  - Follow-ups: `Classify()`, `BotReply()`, `SendReply(template)`, `PauseColleagues()`, `StopAllOutreach()` (= `ToolStop` + `WaCancelReminder`)
- Produces `decide(lead: LeadSnapshot, event: EventSnapshot, now) -> list[Action]`. `EventSnapshot.kind` covers:
  - Email: `sent, open, click, reply, bounce_hard, bounce_soft, unsubscribe`
  - WhatsApp: `wa_sent, wa_delivered, wa_read, wa_failed (data.code), wa_button (data.payload), wa_text`
  - Cal.com: `booking_created, no_show, meeting_ended`

- [ ] **Step 1: Write the tests (one per row).**

  **Email rows:**

  | Test | Lead + event | Expected |
  |---|---|---|
  | `test_hard_bounce_no_wa` | email lead, `bounce_hard` | `Suppress(email)`, `ToolStop`, `SetState(SUPPRESSED)` |
  | `test_hard_bounce_with_wa_switches` | has WA opt-in, not switched | `Suppress(email)`, `ToolStop`, `SwitchChannel("bounce")` |
  | `test_third_soft_bounce_is_hard` | count 2, `bounce_soft` | same as hard |
  | `test_soft_bounce_waits` | count 0 | `[]` |
  | `test_bot_click_ignored`, `test_machine_open_ignored` | | `[]` |
  | `test_human_open` | | `[AddScore(n)]` |
  | `test_third_open_team_invite` | | includes `SetVariant("team_invite")` |
  | `test_homepage_click` | | `[AddScore(2)]` |
  | `test_case_study_click` | | `[AddScore(5), SetVariant("case_study")]` |
  | `test_pricing_click_hot` | | `AddScore(10)`, `Alert("hot_lead")`, `SetVariant("booking_slots")`, `SendReply("booking_slots")` |
  | `test_booking_click` | | `[AddScore(10)]` |
  | `test_score_crossing_hot_alerts_once` | | `Alert("hot_lead")` once (`flags["hot_alerted"]`) |
  | `test_email_reply` | | `StopAllOutreach`, `SetState(ENGAGED)`, `Classify`, `PauseColleagues` |
  | `test_unsubscribe_any_state` | | `SuppressAllChannels`, `StopAllOutreach`, `SetState(SUPPRESSED)` |
  | `test_unsubscribe_link_click_any_verdict` | | same as unsubscribe |
  | `test_not_opened_next_new_subject` | | includes `SetVariant("new_subject")` |

  **WhatsApp rows:**

  | Test | Lead + event | Expected |
  |---|---|---|
  | `test_wa_failed_not_on_whatsapp_with_email` | `wa_failed` code 131026, has email | `SwitchChannel("wa_undeliverable")` |
  | `test_wa_failed_not_on_whatsapp_no_email` | same, no email | `SetState(CLOSED,"unreachable")` + `Suppress(phone)` |
  | `test_wa_marketing_limit` | code 131049 | same as 131026 (switch if possible, else `NURTURE`) |
  | `test_wa_user_stopped_marketing` | code 131050 | `SuppressAllChannels`, `StopAllOutreach`, `SetState(SUPPRESSED)` |
  | `test_wa_button_book_demo` | `wa_button` `BOOK_DEMO` | `StopAllOutreach`, `SetState(QUALIFYING)`, `WaSendText("booking_link")`, `PauseColleagues` |
  | `test_wa_button_ask_question` | `ASK_QUESTION` | `StopAllOutreach`, `SetState(ENGAGED)`, `WaSendText("ask_away")`, `PauseColleagues` |
  | `test_wa_optout_keyword` | `wa_text` "STOP " | `SuppressAllChannels`, `StopAllOutreach`, `SetState(SUPPRESSED)`. **No** `Classify` |
  | `test_wa_text_first_time` | `wa_text`, state `IN_SEQUENCE` | `StopAllOutreach`, `SetState(ENGAGED)`, `Classify`, `PauseColleagues` |
  | `test_wa_text_in_bot_chat` | `wa_text`, `ENGAGED`, no human owner | `[BotReply()]` |
  | `test_wa_text_human_owns_chat` | `wa_human_owner` set | `[]` (stored for the human only) |
  | `test_wa_read_hint` | `wa_read` | `[AddScore(1)]` once |

  **Cross-channel and terminal rows:**

  | Test | Lead + event | Expected |
  |---|---|---|
  | `test_email_reply_cancels_wa_reminder` (**Review Focus 1**) | both channels, active `whatsapp`, email `reply` | includes `WaCancelReminder` |
  | `test_wa_tap_stops_email` (**Review Focus 1**) | active `email` after a switch, `wa_button` | includes `ToolStop` |
  | `test_terminal_state_no_actions` (**Review Focus 3**) | `SUPPRESSED`/`HANDED_OFF`/`CLOSED`/`REJECTED`/`ARCHIVED` + pricing click / `BOOK_DEMO` | `[]` |
  | `test_terminal_still_records_optout` | `CLOSED` + `wa_text` "stop" | `SuppressAllChannels` only |
  | `test_booked_event` | `booking_created` | `StopAllOutreach`, `SetState(BOOKED)`, `Alert("booked")`, `PauseColleagues` |
- [ ] **Step 2: Run `pytest tests/test_rules.py -v`.** Expected: FAIL.
- [ ] **Step 3: Implement `decide`.** Order of checks: opt-out / unsubscribe / hard bounce first (they apply even in terminal states), then the terminal guard, then verdict ≠ human → `[]` (for open/click/read only), then per-kind rules.
- [ ] **Step 4: Run the tests.** Expected: pass.
- [ ] **Step 5: Commit** `feat: pure rules engine for email + WhatsApp events`.

---

### Task 6: Email tool adapter + webhook receiver + click redirect + executor

**Files:**
- Create: `src/outreach/emailtool/base.py`, `fake.py`, `<tool>.py`, `alerts.py`, `app.py`; add `execute()` to `actions.py`
- Test: `tests/test_tool_adapter.py`, `test_webhooks_email.py`, `test_redirect.py`, `test_execute.py`

**Interfaces:**
- Consumes: Task 1 fixtures, `parse_token`, `classify_event`, `decide`, `transition`
- Produces:
  - `EmailTool` protocol:
    - `add_lead(campaign_id, lead, variables) -> str`
    - `update_variables(tool_lead_id, variables)`
    - `pause`, `resume`, `stop`
    - `reply(tool_lead_id, body) -> bool`
    - `parse_webhook(headers, body) -> list[NormalizedEvent]` (raises `BadSignature`)
    - `mailbox_stats(campaign_id, since) -> {"sent","bounced"}`
  - `NormalizedEvent(provider_event_id, channel, kind, occurred_at, tool_lead_id|None, email|None, phone|None, step, ip, user_agent, data)`. This is shared with WhatsApp in Task 8.
  - `ingest(session, events: list[NormalizedEvent], deps, now) -> None`. It matches each event to a lead, stores it idempotently and runs `decide` + `execute` (clicks are deferred to the worker). Task 8 and Task 11 reuse it.
  - `execute(session, lead, actions, deps, now)`, where `deps = Deps(tool, wa, alerter, claude)`
  - `TeamsAlerter.send(kind, text, lead)` writes an `Alert` row; a Teams failure doesn't raise
  - Routes: `POST /webhooks/email`, `GET /r/{token}` (302; bad token → homepage), `POST /intake`

- [ ] **Step 1: Write the tests.**
  - adapter: each fixture maps to the expected `NormalizedEvent`; a bad signature → `BadSignature`.
  - webhooks:
    - `test_duplicate_webhook_acts_once` (**Review Focus 2**)
    - `test_reply_matched_by_tool_lead_id`
    - `test_reply_from_colleague_matches_by_domain` (**Review Focus 5**)
    - `test_reply_unmatched_alerts`
    - `test_bad_signature_401`
  - redirect: `test_redirect_logs_and_302`, `test_bad_token_goes_home`, `test_unsubscribe_link_suppresses_immediately`
  - execute:
    - `test_pause_colleagues` (pauses `IN_SEQUENCE` colleagues on either channel, calls `tool.pause` / `wa` reminder cancel)
    - `test_execute_stop_calls_tool`
    - `test_suppress_all_channels_writes_both_rows`
- [ ] **Step 2: Run the tests.** Expected: FAIL.
- [ ] **Step 3: Implement.** Idempotency comes from the unique `provider_event_id` (catch `IntegrityError` → skip). Run one DB transaction per event. `SwitchChannel` / `WaSend*` are executed by calling into `outreach.py` / `whatsapp/client.py`. Stub them in this task's tests with the fakes.
- [ ] **Step 4: Run `pytest -q`.** Expected: pass.
- [ ] **Step 5: Commit** `feat: email adapter, shared ingest, click redirect, action executor`.

---

### Task 7: Email sequencer + templates (§5g, §5h)

**Files:**
- Create: `src/outreach/sequencer.py`, `templates/emails.yaml`, `tests/test_sequencer.py`

**Interfaces:**
- Produces:
  - `SCHEDULE_DAYS = {1:0, 2:4, 3:9, 4:15}`, `END_WAIT_DAYS = 5`
  - `render_step(lead, step) -> {"subject_N","body_N"}`. Variant priority: `booking_slots > case_study > product > team_invite > new_subject > standard` (`new_subject` affects the subject only).
  - `enroll(session, lead, tool)`, `refresh_next_step(session, lead, tool)`
  - `end_of_sequence_route(lead, human_opens, human_clicks) -> (track, list[Action])`. Branches in order:
    - hot → `sales_personal` + `Alert("hot_no_reply")`
    - zero human opens and a WhatsApp fallback available → `SwitchChannel("never_opened")` (§5h A)
    - clicks → `warm_2_3w`
    - opens → `monthly`
    - else `slow_3m`

    Every branch except the switch sets `NURTURE`.

- [ ] **Step 1: Write the tests.**
  - `test_templates_have_all_keys` (steps 1–4 × `standard, new_subject, case_study, product, team_invite, booking_slots`; bodies contain `{booking_link}` and `{unsubscribe_link}`)
  - `test_no_spying_phrases`
  - `test_render_uses_tracked_links`
  - `test_variant_priority`
  - `test_enroll_calls_tool_once_and_stores_id`
  - one `test_end_route_*` per branch, including `test_end_route_never_opened_switches_to_whatsapp`
- [ ] **Step 2: Run the tests.** Expected: FAIL.
- [ ] **Step 3: Implement.** Placeholder copy is marked `# MARKETING: replace`.
- [ ] **Step 4: Run the tests.** Expected: pass.
- [ ] **Step 5: Commit** `feat: email sequencer, variant templates, end-of-sequence routing`.

---

### Task 8: WhatsApp client + webhook (§7)

**Files:**
- Create: `src/outreach/whatsapp/client.py`, `whatsapp/webhook.py`, `templates/whatsapp.yaml`; add routes `GET/POST /webhooks/whatsapp` in `app.py`
- Test: `tests/test_wa_client.py`, `tests/test_webhooks_wa.py`

**Interfaces:**
- Consumes: `ingest`, `NormalizedEvent`, Task 1 WhatsApp fixtures
- Produces:
  - `class WhatsAppClient` (and `FakeWhatsApp` with the same methods):
    - `send_template(phone, name, lang="en", variables: list[str]) -> str` (the `wamid`)
    - `send_text(phone, text) -> str`. Raises `WindowClosed` locally if `lead.wa_window_until <= now`, *before* calling Meta.
    - `send_cta_link(phone, text, url) -> str`
  - Both call `POST https://graph.facebook.com/{wa_graph_version}/{wa_phone_number_id}/messages` with a Bearer token.
  - `parse_wa_webhook(headers, body) -> list[NormalizedEvent]`, which verifies `X-Hub-Signature-256` = HMAC-SHA256(raw body, `wa_app_secret`). It maps:
    - `statuses[].status` sent/delivered/read/failed → `wa_sent`/`wa_delivered`/`wa_read`/`wa_failed` (with `data.code` from `errors[0].code`)
    - `messages[]` type `button`/`interactive` → `wa_button` (`data.payload`)
    - type `text` → `wa_text` (`data.text`)
    - `provider_event_id` = message id + status
  - `GET /webhooks/whatsapp` answers Meta's `hub.challenge` when `hub.verify_token == wa_verify_token`.
  - On every inbound `wa_text` / `wa_button`, `ingest` sets `lead.wa_window_until = occurred_at + 24h` and stores a `ChatMessage`.

- [ ] **Step 1: Write the tests.**
  - `test_verify_challenge` (right token → challenge echoed; wrong token → 403)
  - `test_bad_signature_401`
  - `test_parse_each_fixture` (7 fixtures → the right kinds and codes)
  - `test_inbound_opens_24h_window`
  - `test_send_text_outside_window_raises_without_http_call` (respx asserts no call)
  - `test_duplicate_status_acts_once` (**Review Focus 2**)
  - `test_unknown_number_alerts_and_no_reply` (**Review Focus 5**): an inbound from a number with no lead → `Alert("unmatched_inbound")`, no bot reply, no lead created
  - `test_template_payload_shape` (respx captures the JSON: `type: "template"`, `template.name == "ozoo_intro"`, language code)
- [ ] **Step 2: Run the tests.** Expected: FAIL.
- [ ] **Step 3: Implement.** `templates/whatsapp.yaml` holds the template names, their variable order and the free-form texts (`booking_link`, `ask_away`, `handoff_notice`, `reschedule`), with copy marked `# MARKETING: replace`. The free-form texts must not contain a raw URL; the booking URL is added via `make_link(..., channel="whatsapp")`.
- [ ] **Step 4: Run the tests.** Expected: pass.
- [ ] **Step 5: Commit** `feat: WhatsApp Cloud API client and webhook with 24h window enforcement`.

---

### Task 9: Claude reply classifier, both channels (§6)

**Files:**
- Create: `src/outreach/classifier.py`, `tests/test_classifier.py`; wire `Classify()` in `execute`

**Interfaces:**
- Produces:
  - `ReplyClass(label, confidence, return_date, later_date, referral_email, referral_phone, reason, draft_answer)`
  - `classify_reply(text, channel, client) -> ReplyClass`
  - `actions_for_class(rc, lead) -> list[Action]`

- [ ] **Step 1: Write the tests** (mocked client).
  - Mapping:
    - `interested` → `SetState(QUALIFYING)` + booking link (email: `SendReply("booking_link")`; WhatsApp: `WaSendText("booking_link")`)
    - `question` → email: `Alert("question")` with `draft_answer`; WhatsApp: `BotReply()`
    - `not_now` → `NURTURE`, with `nurture_until` = `later_date` or +90 days
    - `not_interested` → `CLOSED`
    - `unsubscribe` → `SuppressAllChannels` + `SUPPRESSED`
    - `ooo` → `PAUSED("ooo")` + `ToolPause(return_date or +7d)`
    - `wrong_person` → `intake_lead` of the referral (`source="referral"`) + `CLOSED`
    - `unsure` → `Alert("unsure")` (on WhatsApp also `WaSendText("handoff_notice")` while the window is open, so the prospect isn't ignored)
  - `test_low_confidence_is_unsure` (0.69)
  - `test_api_error_is_unsure` (**Review Focus 4**)
  - `test_malformed_tool_output_is_unsure`
  - `test_reply_text_is_quoted_not_obeyed` (body inside `<reply>` tags; the system prompt says it's data)
- [ ] **Step 2: Run the tests.** Expected: FAIL.
- [ ] **Step 3: Implement.** Call `client.messages.create(model=settings.classifier_model, max_tokens=500, system=..., tools=[classify_tool], tool_choice={"type":"tool","name":"classify_reply"}, messages=[...])` with a 20 s timeout. The tool schema enumerates the 8 labels. Strip quoted email history before sending.
- [ ] **Step 4: Run the tests.** Expected: pass.
- [ ] **Step 5: Commit** `feat: Claude reply classifier for email and WhatsApp with human fallback`.

---

### Task 10: WhatsApp chatbot + human takeover (§7)

**Files:**
- Create: `src/outreach/chatbot.py`, `src/outreach/handoff.py`, `kb/ozoo_kb.md`; add route `POST /teams/commands` (Task 10b; was a Slack slash-command route in Task 10)
- Test: `tests/test_chatbot.py`, `tests/test_handoff.py`

**Interfaces:**
- Produces:
  - `BotResult(kind: "answer"|"ready_to_book"|"wants_human"|"not_ready"|"out_of_scope", text: str|None)`
  - `bot_reply(lead, history: list[ChatMessage], kb: str, client) -> BotResult`
  - `execute(BotReply())` routes the result:
    - `answer` → `send_text`
    - `ready_to_book` → `QUALIFYING` + booking link
    - `wants_human` / `out_of_scope` → `start_takeover`
    - `not_ready` → `NURTURE` + a polite close text
  - `start_takeover(session, lead, reason, alerter, now)` sends a Teams alert with the transcript and the `wa` command usage. It sets `variant_flags["takeover_at"]` and sends `handoff_notice` to the lead.
  - Teams `wa <lead_id> <text>` (bot mention in the sales channel) → sets `wa_human_owner` = the person's Entra object id, replies at once, sends in a background task as `author="human"`. `done <lead_id>` → clears the owner (the bot resumes).
  - Teams outgoing-webhook signature: verify `Authorization: HMAC <base64>` with the base64-decoded `teams_outgoing_secret`, reject activity timestamps more than 5 min off, check tenant / channel / user allow-lists, dedupe on the activity id.

- [ ] **Step 1: Write the tests** (mocked Claude).
  - `test_answer_sent`
  - `test_price_question_never_quotes`: the system prompt includes the guardrail lines, and a canned output containing a currency amount not present in the KB is replaced by a handoff (post-check: regex for currency/number + "price"; if found and not in the KB → `out_of_scope`)
  - `test_sixth_turn_offers_booking_or_human` (`wa_bot_turns >= 6` → no LLM call, fixed text with both options)
  - `test_api_error_hands_to_human` (**Review Focus 4**)
  - `test_wants_human_starts_takeover`
  - `test_slash_wa_sends_and_mutes_bot`
  - `test_slash_wa_outside_window_returns_error_text` (Teams reply: "24h window closed — lead must message first", no send)
  - `test_slash_wa_done_releases`
  - `test_teams_signature_valid_wrong_missing_malformed`
- [ ] **Step 2: Run the tests.** Expected: FAIL.
- [ ] **Step 3: Implement.** Use a Claude forced tool call (`tool_choice` → `bot_reply` with `kind` enum + `text`), `max_tokens=400`. KB content goes in the system prompt inside `<kb>` tags, and the chat history goes in as messages. `kb/ozoo_kb.md` starts as a structured placeholder (services, who it's for, process, FAQs), marked `# MARKETING: fill before go-live`.
- [ ] **Step 4: Run the tests.** Expected: pass.
- [ ] **Step 5: Commit** `feat: WhatsApp chatbot with guardrails and Slack human takeover`.

---

### Task 11: Cal.com qualify + book + handoff (§8, §9)

**Files:**
- Create: `src/outreach/booking.py`, `tests/test_booking.py`; add route `POST /webhooks/cal`

**Interfaces:**
- Produces:
  - `fit_from_answers(size, timeline) -> "high"|"medium"|"low"`:
    - high = ≥ 50 employees AND ≤ 3 months
    - medium = ≥ 10 OR ≤ 6 months
    - else low

    The sales lead confirms these rules and they live in config. The Cal.com routing form uses the same rule (high → PM demo, medium → sales call, low → thank-you page).
  - `handle_cal_event(session, payload, deps, now)`, built on `ingest`. Leads are matched by the `metadata.lead` token first, then by attendee email, then by attendee phone.

- [ ] **Step 1: Write the tests** using the fixtures.
  - `test_booking_created_moves_to_booked`
  - `test_booking_from_whatsapp_lead` (token with `channel="whatsapp"`)
  - `test_match_by_metadata_token_over_email` (**Review Focus 5**)
  - `test_unmatched_booking_alerts`
  - `test_form_submitted_low_fit_to_nurture`
  - `test_no_show_first_time` (`Alert("no_show")` + a reschedule message on the lead's active channel; `BOOKED→QUALIFYING`)
  - `test_no_show_second_time_to_nurture`
  - `test_meeting_ended_hands_off`: `HANDED_OFF` + Teams summary containing both channels' history (emails sent, reply text, WhatsApp transcript, score, form answers)
  - `test_duplicate_booking_webhook_acts_once`
  - `test_bad_signature_401`
- [ ] **Step 2: Run the tests.** Expected: FAIL.
- [ ] **Step 3: Implement.** Verify `X-Cal-Signature-256`. Reminders (24 h / 1 h) are Cal.com workflows set in its UI (Task 13), not our code.
- [ ] **Step 4: Run the tests.** Expected: pass.
- [ ] **Step 5: Commit** `feat: Cal.com booking, fit routing, no-show, handoff summary`.

---

### Task 12: Outreach starter + worker tick + mailbox health

**Files:**
- Create: `src/outreach/outreach.py`, `worker.py`, `health.py`, `tests/test_outreach.py`, `tests/test_worker.py`, `tests/test_health.py`; start `tick` every 60 s from the FastAPI lifespan when `RUN_WORKER=1`

**Interfaces:**
- Produces:
  - `start_outreach(session, lead, deps, now)`: on `email` → `sequencer.enroll`; on `whatsapp` → `send_template("ozoo_intro")` + set `wa_template_sent_at`.
  - `switch_channel(session, lead, reason, deps, now)`: when `fallback_channel` is None → `NURTURE`. Otherwise it stops the current channel, sets `active_channel` and `channel_switched=True`, and calls `start_outreach`.
  - `tick(session, deps, now) -> dict[str,int]`. Jobs run in this order, each in its own transaction:
    1. `start_new`: `IN_SEQUENCE` leads not yet started → `start_outreach` (max 50 per tick)
    2. `process_settled_clicks` (older than 30 s)
    3. `booking_click_not_booked` (30 min → booking-slots reply on the active channel, or an alert)
    4. `wa_reminder`: active `whatsapp`, template sent ≥ 48 h ago, no inbound, no reminder → `send_template("ozoo_reminder")`
    5. `wa_no_response`: reminder sent ≥ 48 h ago, no inbound → `switch_channel("wa_no_response")`
    6. `resume_paused` (`ooo`) + `holds.resume_account_holds` (account rule) + `holds.release_eu_holds` (only when `EU_EMAIL_APPROVED`)
    7. `end_of_sequence` (email `step_sent == 4`, last send + 5 d)
    8. `wa_sla`: takeover with no human message within 1 business hour → `Alert("wa_sla_missed")` once
    9. `attended_check` (`BOOKED`, meeting ended > 2 h ago, no no-show flag → handoff)
    10. `decay_scores`
    11. Daily `check_mailboxes`
  - `check_mailboxes(session, tool, alerter, now) -> list[str]`

- [ ] **Step 1: Write the tests.**
  - outreach:
    - `test_start_email_lead_enrolls`
    - `test_start_wa_lead_sends_intro_template`
    - `test_switch_once_then_nurture`
    - `test_both_channels_only_one_sends` (**Review Focus 1**): a lead with both → exactly one of `tool.add_lead` / `wa.send_template` called
  - worker:
    - `test_click_not_processed_before_30s`
    - `test_two_quick_clicks_both_bot_after_settle`
    - `test_wa_reminder_at_48h` (47 h → none, 48 h → sent once)
    - `test_wa_no_response_switches_to_email`
    - `test_wa_no_response_no_email_goes_nurture`
    - `test_reply_before_reminder_cancels_it` (**Review Focus 1**)
    - `test_ooo_resumes_on_date`
    - `test_tick_resumes_account_holds`, `test_tick_releases_eu_holds_only_when_approved`
    - `test_end_of_sequence_after_5_days`
    - `test_wa_sla_alert_after_1_business_hour` (Friday 17:30 takeover → alert Monday 09:30, not Friday 18:30)
    - `test_attended_check_hands_off_after_2h`
    - `test_tick_is_idempotent`
  - health: `test_bounce_rate_over_2pct_pauses`, `test_under_50_sends_not_judged`
- [ ] **Step 2: Run the tests.** Expected: FAIL.
- [ ] **Step 3: Implement.** One failing job is logged and doesn't block the others.
- [ ] **Step 4: Run `pytest -q`.** Expected: the whole suite passes.
- [ ] **Step 5: Commit** `feat: channel starter/switcher, worker tick, mailbox health`.

---

### Task 13: Deploy + end-to-end test, both channels (§18 step 6)

**Files:**
- Create: `Dockerfile`, `docs/runbook.md`, `scripts/e2e_checklist.md`

- [ ] **Step 1: Write the Dockerfile.** Base image `python:3.12-slim`, running `uvicorn outreach.app:app --host 0.0.0.0 --port $PORT`, with `init_db()` at startup. Deploy to Render or Railway with Postgres. Point `go.ozoo.ai` at the service.
- [ ] **Step 2: Configure the external UIs** and record every setting in `docs/runbook.md`:
  - **Email tool:** webhooks → `/webhooks/email`; link tracking OFF; one campaign per timezone group using `{{subject_N}}` / `{{body_N}}`.
  - **Meta:** webhook → `/webhooks/whatsapp`, subscribed to `messages`; test recipients registered.
  - **Cal.com:** routing form + 2 event types + webhook → `/webhooks/cal` + 24 h / 1 h reminders.
  - **Microsoft Teams:** a Workflows "When a Teams webhook request is received" flow for alerts, plus an outgoing webhook (Manage team > Apps > Create an outgoing webhook) → `/teams/commands` for `wa` / `done`.
- [ ] **Step 3: Run the e2e checklist** with 3–5 test inboxes and 2–3 test WhatsApp numbers. Set the timings to minutes (`SCHEDULE_DAYS`, reminder 48 h → 2 min via env override). For each row, compare the expected state with the DB:

  | Lead type | Trigger | Expected |
  |---|---|---|
  | Email only | Gmail open / Apple Mail open | human +1 / `machine` |
  | Email only | Pricing click | `hot_lead` alert |
  | Email only | Reply "interested" | `QUALIFYING`, booking link emailed |
  | Email only | Reply "not now, try in March" | `NURTURE`, March date |
  | Email only | OOO auto-reply | `PAUSED`, resumes |
  | Email only | Unsubscribe link | `SUPPRESSED` (email + phone rows) |
  | Email only | Fake address | `bounce_hard` → `SUPPRESSED` |
  | WhatsApp only | Tap **Book demo** → book | `QUALIFYING` → `BOOKED`, `booked` alert |
  | WhatsApp only | Tap **Ask a question**, ask 2 KB questions | bot answers |
  | WhatsApp only | Ask about custom price | handoff, Teams alert, `wa` reply reaches the phone |
  | WhatsApp only | Send "STOP" | `SUPPRESSED`, no further messages |
  | WhatsApp only | No response | reminder at 2 min, then `NURTURE` |
  | Both | No WhatsApp response | reminder, then email sequence starts; no further WhatsApp messages |
  | Both | Number not on WhatsApp | `wa_failed` → email starts |
  | Both | WhatsApp tap while email active (after switch) | email tool lead stopped |
  | Both | Meeting held | `HANDED_OFF`, summary includes both channels |
  | Any | Re-send the same webhook manually | no duplicate effect |
- [ ] **Step 4: Fix failures** with superpowers:systematic-debugging and re-run until every row passes.
- [ ] **Step 5: Commit** `chore: deploy config, runbook, e2e checklist (all rows passing)`.

---

## Not in this plan (next iterations)

Nurture email/WhatsApp *sending* (v1 sets `nurture_track` / `nurture_until` only) · CRM sync + sales SLA accept/reject (§9) · website tracking script · Google Postmaster API · dashboard (§13) · AI voice call.
