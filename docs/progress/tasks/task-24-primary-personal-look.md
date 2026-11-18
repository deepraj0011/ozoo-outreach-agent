# Task 24: personal look for the Primary inbox

**Date:** 2026-11-18 · **Status:** done · **Tests:** 1839 passing

## Asked

"Why do mails sometimes go to spam, sometimes Primary, sometimes Promotions? I want every mail in
Primary for any platform." Explained: per-recipient filtering; in the live test the trycloudflare.com
links/images (Spam) and the open pixel plus pictures (Promotions). Chose "personal look".

## Done

- `templates/emails.yaml`: no `[[image]]` and no picture settings in emails 1 and 2 (all variants).
- Live test: `EMAIL_OPEN_PIXEL=0`.
- Guide updated (`docs/guides/email-templates.md` section 2); tests: emails 1-2 render the button and
  the logo mark but no picture; the angle-image test moved to email 3.

## Still needed for real Primary placement (only the team can do it)

Own sending domain and tracking domain, SPF/DKIM/DMARC, 2-4 weeks warm-up, verified lists, seed tests.
