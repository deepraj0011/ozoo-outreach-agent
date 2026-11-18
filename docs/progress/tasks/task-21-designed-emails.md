# Task 21: designed follow-ups, real Ozoo.ai links, replies grounded in the email sent

**Date:** 2026-11-18 · **Status:** done · **Tests:** 1794 passing (23 new in `tests/test_rich_email.py`)

## Asked

"The emails should be more interactive … templates that include images, click on images, Ozoo.ai
links, proper demo links … more but different mails, and the email reply should be based on those
emails … follow-ups based on that." Choices: **hybrid** (email 1 plain, 2-4 designed), images
**taken from ozoo.ai**, the **real** demo link and pages from ozoo.ai.

## Built

- `src/outreach/richmail.py`: turns a step's plain body into a designed HTML email when the step
  version has a `rich:` block: Ozoo header (click → home), the text, a product screen wrapped in a
  tracked link, a purple "book a demo" button (tracked booking link), footer with Post-Call
  Analytics / Voice AI agents / Conversation AI agents / Pricing and Unsubscribe. `[[image]]` and
  `[[button]]` lines in the body place the blocks; the plain-text part drops them. All text is
  HTML-escaped; only links from the template values are used.
- `static/email/`: header cropped from ozoo.ai's share image (the site logo is SVG only), seven
  product screens / photos from ozoo.ai resized to 1200 px. Served by `GET /img/<name>` (strict name
  rule, cached 7 days).
- `sequencer.render_step` returns `html_N` with `subject_N` / `body_N`; the mailpit / gmail /
  mailbox senders send it as the HTML part (plus pixel / trap). Email 1 never has one.
- `templates/emails.yaml`: each angle has a product `page` (`{angle_link}`) and an `image`; steps 2-4
  rewritten with 3 bodies each for the standard version and designed versions of every variant;
  step 3 points to the real free trial (AWS Marketplace). Email 1 gains one product link.
- New link tags `voice`, `chat`, `trial` (links, score, rules, demo console, local setup defaults
  now point at the real ozoo.ai pages).
- Follow-ups follow behaviour: a **verified visit** now switches the next email's version like a
  click (once per version).
- Automatic answers get the email the lead replied to as context; reply copy mentions the topic.
- Booking links keep the calendar URL's own flags (Microsoft Bookings) and add `metadata[lead]`.

## Not done / open

- Microsoft Bookings has no webhook here: bookings there aren't detected (go-live checklist).
- Instantly (`EMAIL_MODE=real`) receives `html_N` as a variable but its campaign template must use it.
