# Documentation

Start with **onboarding**, then pick the guide for what you want to do.

## 1. Onboarding (read first, about 20 minutes)

| Doc | What it covers |
|---|---|
| [What is this repo?](onboarding/01-what-is-this-repo.md) | What the agent does, the technology, the folder map, running it in 5 minutes, where a reviewer should look first |
| [How it works](onboarding/02-how-it-works.md) | A lead's journey: intake, email, WhatsApp, AI reply reading, booking, life stages, how the code decides |
| [Does tracking work?](onboarding/03-tracking-explained.md) | Opens, clicks, unsubscribes and bots (Gmail, Apple, Outlook/Defender, bank gateways): what can be trusted, how bots are filtered, where it can still break, how to check it yourself |

## 2. Guides (how to do things)

| Guide | Use it when |
|---|---|
| [Local testing](guides/local-testing.md) | Running the agent on your laptop with every service fake; scenarios, dev commands, switching one service to real |
| [Demo script](guides/demo-script.md) | Giving the 10-minute demo from the web app |
| [Real demo](guides/real-demo.md) | A free demo with real Gmail, the Meta WhatsApp test number and a real AI model |
| [Visit tracking](guides/visit-tracking.md) | Installing the one-line reading snippet on the website so the agent sees verified visits (a person read the page) |
| [Email templates](guides/email-templates.md) | The Templates screen (preview, write, approve, send by hand) and what gets an email into the Primary inbox |
| [Mailbox mode](guides/mailbox-mode.md) | Sending email from our own mailboxes (`EMAIL_MODE=mailbox`): domain, DNS, warm-up, Reacher verification |

## 3. Plan (what we agreed to build)

| Doc | |
|---|---|
| [Outreach agent plan v2](plan/outreach-agent-plan-v2.md) ([PDF](plan/outreach-agent-plan-v2.pdf)) | The spec: flows, rules, tracking, compliance |
| [Email and WhatsApp flows](plan/email-whatsapp-flows.pdf) | The original one-page flow diagrams |
| [Pilot plan](plan/pilot-plan.md) | Phases from a free internal test to production, with costs |
| [Implementation plan v1](plan/implementation-plan-v1.md) | The task-by-task build plan the code follows |

## 4. Reference

| Doc | |
|---|---|
| [Tool capabilities](reference/tool-capabilities.md) | What Instantly / Smartlead / Meta / Cal.com support, from their docs (items to confirm on a trial account are marked) |
| [Architecture](progress/architecture.md) | Module map and how an event flows through the code |
| [Decisions](progress/decisions.md) | Every binding ruling, with why and the cost if wrong |

## 5. Progress

| Doc | |
|---|---|
| [Status board](progress/README.md) | Every task, its commits and test count |
| [Go-live checklist](progress/go-live-checklist.md) | What must happen before real sending |
| [Task notes](progress/tasks/) | One note per task: what was built, review findings, deferred items |

## Sample data

[`samples/sample_leads.xlsx`](samples/sample_leads.xlsx): six made-up leads (email only, WhatsApp only, both, EU, Singapore, missing contact) to import in the web app.
