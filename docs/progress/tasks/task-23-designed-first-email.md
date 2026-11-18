# Task 23: designed first email

**Date:** 2026-11-18 · **Status:** done · **Tests:** 1838 passing

## Asked

"Every mail should be designed, because receiving the first mail as plain text does not look good."

## Built

- `templates/emails.yaml` step 1: every variant (standard x2, case study, product, pricing slots) now has
  a `rich:` block: the role's product screen, a caption, and a button ("Book a 30-minute demo",
  "Walk me through it", "Choose a 30-minute slot"). One standard version leads with the role's use case.
- `template_library.py`: the "email 1 stays plain" rule is gone; `demo_templates.js` shows the picture
  and button controls for email 1 too.
- Tests updated: every step renders HTML.

## Trade-off

Told the user before: a picture in the very first cold email makes Promotions more likely. The guide
says how to try a version without the picture from the Templates screen.
