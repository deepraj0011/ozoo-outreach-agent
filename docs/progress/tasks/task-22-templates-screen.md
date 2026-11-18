# Task 22: Primary-first polished emails and the Templates screen

**Date:** 2026-11-18 · **Status:** done · **Tests:** 1838 passing (42 new in `tests/test_template_library.py`, 2 in `tests/test_rich_email.py`)

## Asked

"Mails dropped in primary box, more professional, best template… good colors matching Ozoo… book
demos and other relevant details… good images from Ozoo with best scenarios and use cases. Also an
option on the UI where the product manager can approve, write his own, choose the best templates and
send mails." Choices: **Primary-first, polished**; the PM screen does **browse & preview, approve /
switch off, write & edit, send manually**; **the PM approves everything**.

## Built

- `richmail.py` redesigned: letter-like HTML (white, left-aligned, Ozoo ink and purple), `> ` use-case
  box, `- ` lists, one product screen, one purple button, signature with the Ozoo mark (rendered from
  ozoo.ai's SVG logo into `static/email/ozoo-mark.png`), quiet unsubscribe line. No banner, no hidden
  preheader. `[[button]]` becomes "Book a 30-minute demo: <link>" in the plain-text part.
- `templates/emails.yaml`: a `usecase` per role angle (two each, all KB facts, matched to the angle's
  screen); emails 2-4 rewritten with one text link each.
- Versions in `sequencer.py` (`step_versions`, `live_versions`, `choose_version`, `render_version`).
- `template_library.py` + `template_console.py` + `demo_templates.js`: the Templates screen
  (library by email, preview as any role on desktop / phone, editor with live preview and picture
  picker, approve / switch off with the passcode, send-by-hand tab). New table `email_template`.
- Email tools: `reply(..., html=None)` carries a designed part (mailpit / gmail / mailbox).
- Fix: designed `html_N` now reaches the email tool on enrolment and refresh.
- Guide: `docs/guides/email-templates.md` (the screen, and what gets mail into Primary).

## Checked

Isolated copy on port 8011 (own env and DB): created a template in the editor, approved it with the
passcode, sent it by hand to the two eligible sample leads; ineligible leads showed the reason; no
console errors.

## Security review

Independent review found no critical or high issue. Fixed: DNS-rebinding guard (Host / Origin), passcode lockout, plain placeholders only, no lead-supplied URLs in emails.

## Open

- No sign-in on the web app: names are self-declared (go-live checklist).
- Primary placement depends on domain setup (guide section 2); the live test's tunnel URLs hurt it.
