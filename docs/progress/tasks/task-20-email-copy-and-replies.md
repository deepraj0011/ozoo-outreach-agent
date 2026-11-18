# Task 20: real email copy, variety, and automatic replies

**Status:** ✅ Done (2026-11-18)

## Why

The live test exposed four problems:
- A reply asking "Can I get more info about this?" got no answer at all; only a sales alert went out.
- The copy was a placeholder ("automating outreach"), which is the wrong positioning for Ozoo.
- Every lead got the same four emails, word for word.

## What changed

**Copy** (`templates/emails.yaml`). All of it is new and grounded in `kb/ozoo_kb.md`: no prices, no
tracking language, and each email stands on its own.
- **Angles.** Each lead gets an angle from its role, or failing that its industry: collections,
  quality / compliance, sales, service / CX, technology, or general. The angle fills `{topic}`,
  `{hook}`, `{value}`, `{proof}` and `{ask}`, with alternatives.
- **Alternatives.** A subject or body can be a list of alternatives. `sequencer._pick` chooses one
  with a stable hash of the lead, step and field. A lead always gets the same version, and two
  leads at the same bank get different ones.
- **The four emails:** 1 = an insight and a question; 2 = a proof point (the NBFC proof of concept);
  3 = how to start small (trial, setup under an hour, a pilot on one portfolio); 4 = a break-up note
  with one-word reply options. The interest variants (case study, product, booking slots) were
  rewritten for every step.
- **Signature:** `EMAIL_SIGNATURE` (default "The Ozoo.ai team").

**Automatic replies** (`classifier.actions_for_class`, email leads only):

| Reply | Email sent at once |
|---|---|
| interested | booking link (2 versions) |
| question, answered by the KB | `question_answer`: the answer plus a booking link (`email_answer.py`, chatbot guardrails and post-check) |
| question, not in the KB | `question_ack`: "a colleague will reply within one business day" |
| not now | `not_now_ack`: "I'll check back in <month>" |
| not interested | `not_interested_ack`, then the lead is closed |
| wrong person | `referral_thanks` if they named someone, else `who_is_right` |
| out of office, unsubscribe, unclear | nothing (the sales alert as before) |

Sales still get the alert for every question, with the automatic answer quoted. WhatsApp behaviour
is unchanged.

## Not done

- No delay before automatic replies: they go within seconds. A human-like delay of a few minutes in
  working hours would look more natural.
- No AI-written personal first line.
- "database is locked" on simultaneous writes in local SQLite (seen in the live test) is still open.

## Tests

33 new tests (`tests/test_email_copy.py`, plus the updated classifier and sequencer tests).
Total: 1770 passing.
