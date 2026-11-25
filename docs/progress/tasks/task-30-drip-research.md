# Task 30: Drip-marketing research applied (2026-11-20)

**Asked:** go through several drip-marketing sources, extract what we can use, and add it to the agent.

**Done**
- Research from 15+ sources (Belkins, Instantly, Hunter, Gong, Apollo, Lemlist, HubSpot, Woodpecker, Google, Yahoo, Microsoft, RFC 8058, DPDP Rules, Meta) → `docs/reference/drip-marketing-playbook.md`, each rule mapped to what the agent already does.
- Most rules were already in place (4 emails, 0/4/9/15 days, same-thread follow-ups, one-click unsubscribe, warm-up caps, 2% bounce pause, stop rules, colleague pause, record-only opens).
- **New: per-company cap.** `MAX_ACTIVE_PER_COMPANY` (default 2): worker `start_new` holds an email lead while 2 colleagues (same `domain`) are in a started email sequence; it starts when one leaves the sequence. Free-mail domains and 0 = no cap.
- **New: copy tips.** `copycheck.py` `copy_advice()`: body over 120 words, subject over 7 words or salesy, more than 3 links, or a meeting ask in emails 1–2. Returned by the template preview as `advice` and shown above the preview on the Templates screen.

**Tests:** `tests/test_copycheck.py` (5), 2 in `test_worker.py`, 1 in `test_template_library.py`. Full suite 1958 passing.

**Open (for the PM):** plain-text-only vs designed HTML A/B in the pilot; Google Postmaster Tools for spam rate; legal view on DPDP for cold email (consent rules apply from about 13 May 2027).
