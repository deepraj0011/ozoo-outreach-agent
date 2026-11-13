# Ozoo.ai Outreach Agent: progress board

**What it is.** One outreach agent that takes a lead (email only, WhatsApp only, or both) through an intake gate and runs the right channel. Email is a 4-email sequence sent by a cold-email tool (Instantly). WhatsApp is an opt-in template with two buttons, a reminder and a chatbot. The agent records every event, filters bots, scores engagement, classifies replies with Claude, books demos through Cal.com and alerts sales in Slack. Both channels share **one lead record and one state**. Lead sourcing is out of scope: the agent starts at intake.

Branch: `feat/outreach-agent-v1`. Plan: `docs/superpowers/plans/2026-11-12-outreach-agent-v1.md`. Spec: `docs/Outreach_Agent_Plan_v2.md`.

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
| 9 | Claude reply classifier | 🔨 Building | none | 0 | [task-09](tasks/task-09-reply-classifier.md) |
| 10 | WhatsApp chatbot + human takeover | ⏳ To do | none | 0 | [task-10](tasks/task-10-chatbot-handoff.md) |
| 11 | Cal.com booking + handoff | ⏳ To do | none | 0 | [task-11](tasks/task-11-calcom-booking.md) |
| 12 | Outreach starter + worker + mailbox health | ⏳ To do | none | 0 | [task-12](tasks/task-12-worker-health.md) |
| 13 | Deploy + end-to-end test | ⏳ To do | none | 0 | [task-13](tasks/task-13-deploy-e2e.md) |

**Tests now:** 571 passing (`.venv/bin/pytest -q`).

**What's next:** Task 9 (Claude reply classifier) is being built on top of the plan v2.1 review changes (`0362a60`: no email opt-in, opens logged only, EU email hold, colleague restart rules). Task 10 (WhatsApp chatbot + human takeover) is next. Cut line if time runs short: Task 10 (chatbot) is the one to drop; `ASK_QUESTION` then goes straight to human takeover.

## Files in this folder

- [architecture.md](architecture.md): module map, event flow, module table.
- [decisions.md](decisions.md): every binding ruling, with why and cost if wrong.
- [go-live-checklist.md](go-live-checklist.md): what must happen before real sending.
- `tasks/task-NN-*.md`: one note per task.
