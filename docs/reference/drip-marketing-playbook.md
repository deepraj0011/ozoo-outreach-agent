# Drip marketing playbook (research, 2026-11-20)

What B2B cold-email research says, mapped to what the agent does. Tags: **[P]** official or primary (Google, Yahoo, Microsoft, RFC, Government of India, Meta); **[VD]** vendor data (sample size given); **[VO]** vendor opinion. Vendor numbers are rough guides, and sources often disagree.

## 1. Sequence

| Rule | Evidence | Agent |
|---|---|---|
| 3–5 emails; **never a 5th cold email** | Belkins [VD n=7.5M]: steps 3–5 bring 53.5% of meetings, and a 5th email ~3× unsubscribes and spam complaints. Hunter [VD n=31M]: ~3. Instantly [VD]: 4–7 | Up to 4 emails (`send_plan.py`) ✅ |
| **Email 3 is the main converter** (35.6% of meetings) | Belkins [VD] | Write email 3 as a strong standalone (case study / resource offer), not a "bump" |
| Gaps of 3–4 days early, up to 7 later; whole sequence ~14–21 days | Instantly, Lemlist, Apollo [VO] | Starter schedule days 0 / 4 / 9 / 15 ✅ |
| Follow-ups read like a natural reply, in the same thread | Instantly [VD]: ~30% better | Follow-ups go `Re:` with In-Reply-To ✅ |
| Re-contact after a finished sequence: wait ≥ 90 days | Convention (Pipedrive, Apollo) [VO], no data | Colleague holds cool off 90 days; "not now" default 90 days ✅ |

## 2. Timing

- Tue–Thu best and Friday worst (most auto-replies); 08:00–12:00 local time best (Belkins [VD], Instantly [VD]). The best window beats the worst by only ~1.5–2.5 points, so **copy and targeting matter more than timing.**
- Agent: Mon–Fri 09:30–17:30 IST, with follow-ups switching between afternoon and morning (`sendwindow.py`). Kept as is; `EMAIL_SEND_WINDOW` can narrow it to `Tue-Thu 09:30-12:30` if the pilot shows it helps.

## 3. Copy (checked by `copycheck.py`, shown as "Copy tips" in the Templates preview)

- **Body under ~100 words, 3–4 sentences.** Pitching cuts replies by up to 57% (Gong [VD n=28M]). Instantly: under 80.
- **Subject: 2–6 plain words**, no "!", numbers or sales words (Gong [VD], Hunter [VD]).
- **One CTA.** In emails 1–2 ask a question or offer something (a case study or sample report). A direct meeting ask cut replies by 44% (Gong via Apollo). Keep the booking link for email 3–4 or after a positive reply.
- **Few links, few or no images.** HubSpot [VD]: image-heavy emails got 21% fewer clicks. Hunter [VD]: HTML campaigns bounced much more. *Open decision:* our emails also include a designed HTML part with images (`richmail.py`). Try a plain-text-only test against it in the pilot.

## 4. Deliverability (firm rules)

| Rule | Source | Agent |
|---|---|---|
| SPF + DKIM (2048-bit) + DMARC ≥ p=none, aligned From, PTR, TLS | Google, Yahoo, Microsoft (from 2025-05-05) [P] | Ops setup: go-live checklist |
| RFC 8058 `List-Unsubscribe` (HTTPS) + `List-Unsubscribe-Post: List-Unsubscribe=One-Click`, DKIM-signed; visible body link; POST with no cookies or redirect | RFC 8058, Google [P] | `set_unsubscribe_headers` ✅, body link in every step ✅ |
| Honour opt-out within 48 h | Google, Yahoo [P] | Immediate ✅ |
| Spam complaints < 0.1%, never 0.3% (then 7 days below to recover) | Google [P] | Not measurable by us; watch **Google Postmaster Tools** by hand |
| Bounce < 2% | Instantly [VO], Lemlist [VO] | Mailbox paused above 2% (`health.py`) ✅ |
| 25–35 cold emails per mailbox per day, never > 50; ramp from 10–20 | Woodpecker [VO]; Google: no bursts [P] | `daily_limit` 30, warm-up 10 + 2/day ✅ |
| ~5,000/day to Gmail makes a domain a **permanent** bulk sender | Google [P] | Keep total volume per domain well below |
| **1–2 contacts per company at a time** | Hunter / Reply.io [VD]: 5.1% vs 3.5% at 3+ | **New:** `MAX_ACTIVE_PER_COMPANY=2` (worker `start_new`) |

## 5. Stop and branch

Stop on reply, booking, unsubscribe, bounce ✅. Out-of-office → resume after return ✅. "Not now" → nurture until the date given, else 90 days, then one check-in ✅. Referral → same-company referrals are added automatically ✅. One contact engaged → colleagues pause ✅. Opens are unreliable (Apple MPP inflates them; Lemlist: under 15% suggests a deliverability problem) → record-only ✅.

## 6. Benchmarks (for the pilot)

- Reply rate: average 3.4%, top quartile 5.5% or more (Instantly [VD]); 4.5% per recipient (Hunter [VD]).
- **Banking and insurance are near the bottom by industry** (Belkins [VD]). C-level replies are ~30% lower (Gong [VD]). Expect below-average numbers.
- Meetings ≈ 0.5–2.5% of sends.

## 7. India

- **DPDP Rules 2025:** consent and notice rules apply from about **13 May 2027** (18-month rollout). Consent is the main legal basis, with no "legitimate interest". Withdrawing must be as easy as giving consent, and access or erasure requests must be answered within 90 days. Data a person made public themselves may be exempt (s.3(c)(ii)), so **get legal advice** on cold email to published business contacts.
- **WhatsApp** [P]: opt-in before any business message, opt-outs honoured on or off WhatsApp, templates only outside the 24-hour window. On a Meta per-user limit (error 131049) retry no sooner than 24 h later.

## Sources

Belkins https://belkins.io/blog/sales-follow-up-statistics · https://belkins.io/blog/cold-email-response-rates — Instantly https://instantly.ai/cold-email-benchmark-report-2026 · https://instantly.ai/blog/best-time-to-send-cold-email/ — Hunter https://hunter.io/the-state-of-cold-email — Gong https://www.gong.io/blog/does-cold-email-even-work-any-more-heres-what-the-data-says — Apollo https://www.apollo.io/insights/rewrite-my-cold-email-sequence-steps-to-improve-reply-rates — Lemlist https://lemlist.com/blog/cold-email-benchmarks/ — HubSpot https://blog.hubspot.com/marketing/plain-text-vs-html-emails-data — Woodpecker https://woodpecker.co/blog/how-many-cold-emails-to-send-per-day/ — Google https://support.google.com/a/answer/81126 · https://support.google.com/a/answer/14229414 — Yahoo https://senders.yahooinc.com/best-practices/ — Microsoft https://techcommunity.microsoft.com/blog/microsoftdefenderforoffice365blog/strengthening-email-ecosystem-outlook%E2%80%99s-new-requirements-for-high%E2%80%90volume-senders/4399730 — RFC 8058 https://www.rfc-editor.org/rfc/rfc8058 — DPDP Rules (PIB) https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf — WhatsApp policy https://whatsappbusiness.com/policy/ — Meta per-user limits https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/marketing-templates/per-user-limits/
