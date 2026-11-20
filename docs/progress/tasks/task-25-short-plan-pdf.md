# Task 25: 6-page plan PDF

**Date:** 2026-11-19 · **Status:** done

The old plan PDF (`docs/plan/outreach-agent-plan-v2.pdf`) was 21 pages and still described v2.0 (open-based branches, "try WhatsApp later"). It now has 6 landscape A4 pages with the v2.1 content:

1. Overview: five stages, what changed from the first draft, finish line, v2.1 review changes, the WhatsApp opt-in question
2. Intake gate: four checks diagram, lead contract
3. Email: 4-email sequence and end-of-sequence routes, bot filter, score, sending rules, event → action table
4. Reply classifier, WhatsApp flow + chatbot, WhatsApp 24 h rule, guardrails
5. Qualify + book, handoff, nurture, lead state diagram, colleague restart table
6. System diagram, event sources, funnel metrics, build order, start-now items

Source: `docs/plan/outreach-agent-plan-v2.html`. To rebuild:

```bash
google-chrome --headless=new --no-pdf-header-footer --virtual-time-budget=8000 \
  --print-to-pdf=docs/plan/outreach-agent-plan-v2.pdf "file://$PWD/docs/plan/outreach-agent-plan-v2.html"
```

The long `outreach-agent-plan-v2.md` stays as the engineering spec because the implementation plan and decisions cite its section numbers.
