# Outreach Agent Pilot Plan

13 November 2026

The agent is built and runs locally. We can test it for real for about $0, then pilot email for under
$50 a month, before committing to full production.

## Phases

Each phase runs the same code; only the settings change. We move on only when the previous phase works.

1. **Phase 0: internal WhatsApp test ($0, 1 week).** Meta's free test number sends real WhatsApp
   messages to up to 5 registered phones (our own team). It proves the template, buttons, chatbot,
   human takeover from Teams and Cal.com booking end to end. Runs from a laptop with a free tunnel;
   no hosting.
2. **Phase 1: email pilot (about $40–50/month, 3–4 weeks).** One separate sending domain, 2 low-cost
   mailboxes and the cheapest Instantly plan. The first 2–3 weeks are mailbox warm-up (no sending to
   leads), so start it early. Then a small batch of real leads, plus WhatsApp to the 5 test phones.
3. **Phase 2: production (decide after the pilot).** Meta Business verification, a real WhatsApp
   number (pay per message), always-on hosting and more mailboxes as volume grows. Budgeted only once
   the pilot shows replies and booked meetings.

## What it costs

Phase 1 totals about $40–50 a month on annual billing, or about $55 billed monthly. Prices come from
public pricing pages and reviews; confirm on each vendor's site before paying.

| Item | What it is for | Phase 0 | Phase 1 (pilot) |
| --- | --- | --- | --- |
| WhatsApp Cloud API test number | Real WhatsApp messages to 5 registered phones | $0 | $0 |
| Sending domain (e.g. ozoo-mail.ai) | Keeps cold email away from ozoo.ai | — | ~$1/month (~$12/year) |
| 2 mailboxes (Zoho Mail Lite) | The senders (Google Workspace is ~$7+ each instead) | — | ~$2/month |
| Instantly Growth | Sends the sequence, warms up mailboxes, sends replies back to the agent | — | $37.60/month annual, $47 monthly |
| AI model (Claude Haiku or similar) | Reads replies, runs the chatbot | $0 (rule-based fallback) | ~$1–5/month at pilot volume (approximate) |
| Email verification (ZeroBounce) | Checks addresses before sending | — | ~$0 on free monthly credits at pilot volume (approximate) |
| Cal.com | Demo booking and webhooks | $0 (free plan) | $0 (free plan) |
| Hosting | Receives webhooks | $0 (laptop + free tunnel) | $0–7/month (laptop or small server) |
| Microsoft Teams | Sales alerts and commands | $0 (existing licence) | $0 |

**Open question:** some reviews say Instantly's Growth plan lacks API access, which the agent needs. If
so, the next plan is $77.60/month annual. Confirm with Instantly before buying.

## What needs a yes

Only Phase 1 needs budget; the rest are approvals or small tasks for other teams.

- [ ] **Manager:** approve Phase 0 now and about $50/month for Phase 1.
- [ ] **Manager / IT:** buy the sending domain and set its DNS records (SPF, DKIM, DMARC); start the
      2–3 week warm-up as early as possible.
- [ ] **Teams admin:** allow an outgoing webhook and a Workflows flow in the sales channel.
- [ ] **Meta admin:** a Meta developer app for the free WhatsApp test number. Business verification
      waits for Phase 2.
- [ ] **Legal:** sign-off before emailing EU leads. Until then the agent holds them automatically.
- [ ] **Marketing:** final email and WhatsApp wording, and the facts the chatbot may answer from.
- [ ] **Company choice:** which AI provider may read lead replies (Claude, OpenAI, Azure, or a
      self-hosted model).

## Sources

- WhatsApp test number, free to 5 recipients: [Meta Cloud API get started](https://developers.facebook.com/docs/whatsapp/cloud-api/get-started/)
- WhatsApp per-message pricing (Phase 2): [Meta pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
- Instantly plans: [Woodpecker review of Instantly pricing](https://woodpecker.co/blog/instantly-ai-pricing/), [Secret: Instantly pricing](https://www.joinsecret.com/instantly-ai/pricing)
- Zoho Mail Lite: [Capterra: Zoho Mail pricing](https://www.capterra.com/p/174694/Zoho-Mail/pricing/)
- Domain, AI, ZeroBounce and hosting figures are approximate, from memory; check before paying.
