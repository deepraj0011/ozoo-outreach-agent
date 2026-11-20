# Ozoo.ai Outreach Agent: progress board

**What it is.** One outreach agent that takes a lead (email only, WhatsApp only, or both) through an intake gate and runs the right channel. Email is a 4-email sequence sent by a cold-email tool (Instantly). WhatsApp is an opt-in template with two buttons, a reminder and a chatbot. The agent records every event, filters bots, scores engagement, classifies replies with an AI model the company chooses (Claude, OpenAI, Azure OpenAI or any OpenAI-compatible), books demos through Cal.com and alerts sales in Microsoft Teams. Both channels share **one lead record and one state**. Lead sourcing is out of scope: the agent starts at intake.

Branch: `feat/outreach-agent-v1`. Plan: `docs/plan/implementation-plan-v1.md`. Spec: `docs/plan/outreach-agent-plan-v2.md`.

## Status board

| # | Task | Status | Commits | Tests added | Notes |
|---|---|---|---|---|---|
| 1 | Tool spike + project skeleton | ✅ Done | `40cf02d`, `1ad7829` | 2 | [task-01](tasks/task-01-skeleton-spike.md) |
| 2 | Data model + state machine | ✅ Done | `deb7826` | 5 | [task-02](tasks/task-02-data-model-states.md) |
| 3 | Intake gate + channel choice | ✅ Done | `20c111e`, `d093b54` | 31 | [task-03](tasks/task-03-intake-channels.md) |
| 4 | Tracked links + bot filter + score | ✅ Done | `77d728f`, `bb08b6c` | 52 | [task-04](tasks/task-04-links-botfilter-score.md) |
| 5 | Rules engine (both channels) | ✅ Done | `ddcee6f`, `144aa86` | 256 | [task-05](tasks/task-05-rules-engine.md) |
| 6 | Email adapter, webhooks, redirect, executor | ✅ Done (2 fix rounds, review clean) | `b01edd6`, `b615b84`, `8eecca1`, `a521f9b`, `4cb3162` | 114 | [task-06](tasks/task-06-email-adapter-executor.md) |
| 7 | Email sequencer + templates | ✅ Done (review clean) | `69b8fcc` | 24 | [task-07](tasks/task-07-email-sequencer.md) |
| R | Review update (plan v2.1): no email opt-in, opens ignored, no WhatsApp-later, colleague restart, EU hold | ✅ Done | see git log | 41 | [decisions](decisions.md#review-update-2026-11-13-plan-v21) |
| 8 | WhatsApp client + webhook | ✅ Done (1 fix round, review clean) | `f0fd2e5`, `61cdaa3`, `66b0d48` | 49 | [task-08](tasks/task-08-whatsapp-client-webhook.md) |
| 9 | Claude reply classifier | ✅ Done (1 fix round, review clean) | `af4022a`, `721ea7b` | 70 | [task-09](tasks/task-09-reply-classifier.md) |
| 10 | WhatsApp chatbot + human takeover | ✅ Done (2 fix rounds, review clean) | `f792478`, `e76a81b`, `739846b` | 107 | [task-10](tasks/task-10-chatbot-handoff.md) |
| 10b | Replace Slack with Microsoft Teams (alerts + sales commands) | ✅ Done (1 fix round, verified) | `89df43c`, `98867c1` | 77 | [task-10b](tasks/task-10b-teams.md) |
| 11 | Cal.com booking + handoff | ✅ Done (2 fix rounds, reviewed) | `7c4a09c`, `8236b23`, `b2ee102` | 157 | [task-11](tasks/task-11-calcom-booking.md) |
| 9b | Pluggable AI model provider (company chooses: Claude, OpenAI, Azure OpenAI, any OpenAI-compatible) | ✅ Done (1 fix round, verified) | `9630116`, `1929218` | 144 | [task-09b](tasks/task-09b-pluggable-llm.md) |
| 12a | Run AI calls as background jobs (not inside webhooks) | ✅ Done (1 fix round, verified) | `b354fc1`, `b87c6bc` | 40 | [task-12a](tasks/task-12a-background-ai-jobs.md) |
| 12 | Outreach starter + worker + mailbox health | ✅ Done (1 fix round, verified) | `a99c7c0`, `fba97eb` | 109 | [task-12](tasks/task-12-worker-health.md) |
| 13 | Run locally + local end-to-end test (deploy deferred) | ✅ Done (controller smoke-tested) | `43716f9`, `0ae48e1` | 116 | [task-13](tasks/task-13-deploy-e2e.md) · **[local testing guide](../guides/local-testing.md)** |
| F | Final whole-branch review + fix round | ✅ Done | `2b384b5`, `a47ca78` | 103 | [decisions](decisions.md#final-review-2026-11-13) |
| 14 | Mailpit local email mode (`EMAIL_MODE=mailpit`) | ✅ Done (live smoke test with real Mailpit) | see git log | 24 | [task-14](tasks/task-14-mailpit.md) |
| D | Demo console at `/demo` (local only): live leads, journey, timeline, WhatsApp chat, alerts; buttons act as the lead | ✅ Done (smoke-tested in isolation) | see git log | 0 (spike) | [demo script](../guides/demo-script.md) |
| D2 | Real free demo: `EMAIL_MODE=gmail` (Gmail SMTP + IMAP replies), WhatsApp test number, Groq AI, `.env.demo`; fix: intro template now sends quick-reply payloads (real taps arrive as BOOK_DEMO / ASK_QUESTION, not button text) | ✅ Built, startup verified with dummy keys; not yet run with real accounts | see git log | 6 | [real demo guide](../guides/real-demo.md) |
| D3 | Web app at `/` (local only): Overview funnel, Leads with CSV / Excel import (column matching, preview, intake results), lead panel with step tracker + simulate buttons, WhatsApp Conversations with salesperson takeover, Sales alerts, How it works, Settings; `scripts/start_demo` | ✅ Done, smoke-tested in isolation | see git log | 9 | [demo script](../guides/demo-script.md) |
| 15 | Own open tracking (pixel), Microsoft scanner ranges, labelled unsubscribe visits, one-click unsubscribe headers, threaded follow-ups | ✅ Done | see git log | 35 | [task-15](tasks/task-15-tracking-accuracy.md) |
| 16 | Real knowledge base from ozoo.ai; phone numbers: country names, `DEFAULT_PHONE_REGION` (IN), trunk-0 import fix; WhatsApp phone view in the web app (template text + buttons, pre-send preview) | ✅ Done | see git log | 30 | [task-16](tasks/task-16-kb-phones.md) |
| 17 | Free, accurate email layer: `EMAIL_MODE=mailbox` (several SMTP / IMAP mailboxes, daily caps + warm-up, IMAP IDLE replies), Reacher verification, honeypot link, JavaScript-confirmed clicks, auto-reply filter, thread-header reply matching, open pixel off by default | ✅ Done | see git log | 50 | [task-17](tasks/task-17-mailbox-mode.md) |
| R2 | Repo ready for git hosting: `docs/` split into onboarding / guides / plan / reference / samples with an index, front-page README, onboarding docs fixed, architecture doc brought up to date, `.gitignore` covers SQLite WAL files, tool state, editors, scratch notes | ✅ Done | see git log | 0 | [docs index](../README.md) |
| R3 | Plan PDF cut from 21 to 6 pages (v2.1 content, every diagram kept); HTML source in `docs/plan/`; full `.md` spec unchanged | ✅ Done | see git log | 0 | [task-25](tasks/task-25-short-plan-pdf.md) |
| 18 | Trial safety: clicks alone (no reply / booking) send a `click_check` alert instead of `hot_lead` to sales (`CLICK_ALERTS_NEED_CHECK`, default on) | ✅ Done | see git log | 6 | [task-18](tasks/task-18-click-check-alerts.md) |
| 19 | Real leads, real tracking: verified page visits (`/v.js` + `/v/<token>`), clicks alone never email the lead while a check is needed, send windows + time-of-day rotation for our sender, realistic lead simulation (`python -m outreach.world`) | ✅ Done (story 15/15, 100-lead mix 100/100, 0 wrong hot alerts) | see git log | 83 | [task-19](tasks/task-19-real-leads-real-tracking.md) · [visit tracking guide](../guides/visit-tracking.md) |
| 20 | Real email copy + automatic replies: role-based angles (collections, quality, sales, service, technology, general) with alternative subjects / bodies per lead; KB-grounded answers to emailed questions (`email_answer.py`, chatbot guardrails); courtesy replies for not now / not interested / wrong person; `EMAIL_SIGNATURE` | ✅ Done (story 15/15) | see git log | 33 | [task-20](tasks/task-20-email-copy-and-replies.md) |
| 21 | Designed follow-ups: emails 2-4 get an HTML version (Ozoo header, a clickable product screen per role, a "book a demo" button, footer with product links; plain text always sent too), images served at `/img/` from `static/email/`; new link tags voice / chat / trial; verified visits pick the next email's version; automatic answers see the email the lead replied to; Microsoft Bookings links keep their flags | done | [task-21](tasks/task-21-designed-emails.md) |
| 22 | Primary-first polished emails (letter-like design: use-case box, one product screen, purple button, signature with the Ozoo mark; no banner, one text link) with role use cases from the KB; **Templates screen** in the web app: preview as any role on desktop/phone, write and edit with live preview, approve / switch off with the approver passcode, send an approved template by hand to up to 25 leads (in-thread); fixed: designed `html_N` now reaches the email tool on enrolment | done | [task-22](tasks/task-22-templates-screen.md) |
| 23 | Email 1 designed too (the user's call): same letter design as 2-4 with the role's product screen, a use-case box in one version and the demo button; the editor allows a picture and button in email 1 | done | [task-23](tasks/task-23-designed-first-email.md) |
| 24 | For the Primary inbox (the user's call, "personal look"): no product picture in emails 1-2 (design, use-case box, button and signature stay); pictures only in email 3; open-tracking image off in the live test | done | [task-24](tasks/task-24-primary-personal-look.md) |
| 26 | **Plan & schedule** (the team lead's ask, no passcode): the PM sets when email 1 goes and, after each email, which email follows and on which day depending on what the lead did (HOT / read a page / clicked any or one link / otherwise); flow builder, per-lead Email schedule tab (send now, move, change kind, hold, stop), Upcoming emails by day, plan + start at import; the agent still writes each email | done | [task-26](tasks/task-26-send-plans.md) · [guide](../guides/send-plans.md) |
| 27 | **Simpler Email schedule** ("too complex, even I am confused"): one schedule as a plain list (type + "N days after the previous email", optional ⚡ sooner if they show interest, stop after N days); lead tab = one Next card with Send now · Change date · Pause · Stop; old tree plans converted | done | [task-27](tasks/task-27-simple-schedule.md) · [guide](../guides/send-plans.md) |
| 28 | **Tracking vs. the schedule**: after an out-of-office the next email waits a day after the lead is back; interest that arrives after an email is queued but before it goes still switches it to the "sooner" type; "sooner" can also make that email the last; a "not now" lead gets one check-in email on its date; the lead panel shows interest and emails sent sooner | done | [task-28](tasks/task-28-tracking-vs-schedule.md) · [guide](../guides/send-plans.md) |
| 29 | **P0 scheduled emails**: templates + day schedule, own sender, tracking record-only, unsubscribe / bounce / reply / booking stop the rest, per-lead pause / resume / send now / stop, `EMAIL_FLOW=p0`; end-to-end test; final-review fixes. Full suite: 1978 passing | done | [task-29](tasks/task-29-p0-scheduled-emails.md) · [guide](../guides/p0-email-schedule.md) |
| 31 | **Web app redesign**: React + MUI app in `frontend/` (dark teal theme, Campaign board, Leads, Conversations, Templates, Connectors, Settings) served at `/app` from a build (`scripts/build_web`, run by `scripts/run_local`); read-only connectors and settings routes; plain HTML app retired. Full suite: 2028 passing | done | [task-31](tasks/task-31-web-app-redesign.md) |

**Tests now:** 2028 passing (`.venv/bin/pytest -q`).

**What's next:** test locally with [docs/guides/local-testing.md](../guides/local-testing.md). Before any real sending: verify Instantly on a trial account (sent-event step, stop/block/find endpoints, bounce type, pause), Cal.com and Teams payloads, pick the AI provider, have marketing review `kb/ozoo_kb.md` and the WhatsApp template previews, add DB migrations. See [go-live-checklist.md](go-live-checklist.md).

## Files in this folder

- [architecture.md](architecture.md): module map, event flow, module table.
- [decisions.md](decisions.md): every binding ruling, with why and cost if wrong.
- [go-live-checklist.md](go-live-checklist.md): what must happen before real sending.
- `tasks/task-NN-*.md`: one note per task.
