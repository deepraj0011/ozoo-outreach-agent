# Task 16: real knowledge base + phone number handling (2026-11-17)

## Knowledge base (`kb/ozoo_kb.md`)
- Replaced the placeholder with real content from the public ozoo.ai site (read 2026-11-17): what
  Ozoo.ai is (Habilelabs' conversation AI line for Indian lenders), the three products (Post-Call
  Analytics, Voice AI agents, Conversation AI agents), who it is for, engagement and pilot steps,
  pricing structure (plans by call minutes; **no currency amounts**, so the bot hands every amount
  question to a person), security and data, the NBFC proof-of-concept counts, contact hours, FAQs.
- Left out on purpose: regulatory dates (unverified), office list (site pages disagree), the voice
  FAQ language list (conflicts with the product page). Marketing must review before go-live.
- **Finding:** the email copy (`templates/emails.yaml`) and the WhatsApp intro text describe Ozoo.ai
  as "automating outreach and follow-up". The real product is call QA + voice/chat agents for
  lenders. The intro template text in `docs/REAL_DEMO.md` is updated; email copy is still to do.

## Phone numbers
- `eu.country_code()` (was private): ISO code or common English name ("India", "UAE", "UK" -> GB).
- `normalize_phone(raw, country, fallback_region)`: "+"/"00" numbers keep their own country; else
  the lead's country; else `DEFAULT_PHONE_REGION` (new setting, default `IN`, blank = reject).
  Before: "9876543210" with no country, or country "India", was rejected.
- Lead import: an 11-digit number starting with 0 is no longer turned into "+0...".

## Tests
`tests/test_phone_numbers.py` (24); `test_chatbot.py` KB test updated. 1648 passing (with task 17).

## WhatsApp phone view in the web app (Conversations + lead "WhatsApp chat" tab)
- Messages now show as the lead's phone shows them: the intro template's real text with its
  quick-reply buttons (tappable in the demo, acting as the lead), and the booking text with its
  link button. Templates live in Meta, so `templates/whatsapp.yaml` keeps a display-only
  `preview` copy (body + one label per quick reply, validated); keep it identical to Meta.
- Every lead with a phone appears in Conversations. Before anything is sent, a faded preview shows
  the intro the lead will get and why/when: sent at the next tick, on email for now, or never (no
  WhatsApp opt-in).
- `demo_console.chat_view()` / `wa_preview()`; `whatsapp.sender.template_values()` shared with the sender.
- Smoke-tested in an isolated copy (scratch env + DB, port 8765) with headless Chrome screenshots:
  preview, no-opt-in preview, sent intro, tap "Book a demo" -> booking link reply.
