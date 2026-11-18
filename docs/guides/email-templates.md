# Email templates: the Templates screen and the Primary inbox

## 1. The Templates screen (web app → Email templates)

For the product manager and anyone writing copy. Everything here is local to the web app
(`scripts/run_local`); it never answers through a tunnel.

| You want to… | Do this |
|---|---|
| See what a lead gets | **Preview** on any card. Pick a role (Head of Collections, CTO…), a name and a company; switch Desktop / Phone and Designed / Plain text. Links in a preview go straight to the pages, nothing is tracked. |
| Write a new version of an email | **+ New template** (or **Duplicate** on any card). Choose which email (1-4), when it is used (standard, or after the lead read the case study / a product page / pricing) and for which roles. The preview updates as you type. |
| Write a one-off email | **+ One-off email**. It is never sent by the agent, only from **Send an email**. |
| Make it live | **Approve** (the approver passcode). Drafts are never sent. Editing a live template puts it back to draft. |
| Stop using a version | **Switch off** (passcode). The last standard version of an email can't be switched off. |
| Send something now | **Send an email** tab: choose an approved template, tick leads (up to 25), **Send**, passcode. It goes as a reply in each lead's thread, with tracking and unsubscribe. Leads who unsubscribed, closed, or haven't had an email yet are skipped and say why. |

**Who approves:** whoever holds `TEMPLATE_APPROVER_PASSCODE` (the product manager). Without it set,
approving and sending are switched off; drafting and previews still work. Every change records the
name typed at the top of the screen.

**When it takes effect:** a lead's four emails are prepared when it is enrolled, so a newly approved
version reaches leads enrolled from then on (and the next email of a lead whose interest changes).

### Writing the text

- Placeholders (click to insert): `{first_name}` `{company}`; per role: `{topic}` `{hook}` `{value}`
  `{proof}` `{usecase}` `{ask}`; links: `{booking_link}` `{angle_link}` (the role's product page)
  `{case_study_link}` `{pricing_link}` `{trial_link}` `{product_link}` `{voice_link}` `{chat_link}`
  `{homepage_link}` `{unsubscribe_link}` (required); `{signature}`.
- A blank line starts a paragraph. `> ` starts a use-case box. `- ` lines make a list.
  `Label: {some_link}` becomes a text link "Label →".
- `[[image]]` = the picture (choose one, or "the role's own screen"); `[[button]]` = the purple
  demo button. In the plain-text copy the button becomes "Book a 30-minute demo: <link>".
- Facts only from `kb/ozoo_kb.md`. No prices. Never mention opens, clicks or reads.

## 2. Getting into the Primary tab (what the code does, and what it can't)

Nobody can guarantee Primary. Gmail decides per message, mostly from the **sending domain's
reputation** and from whether the email **looks like one person writing to another**.

What the agent does already:

- All four emails are designed but letter-like (email 1 too, since task 23): white, left-aligned, no banner, no hidden preheader,
  one picture, one button, one text link besides Unsubscribe. A plain-text copy always goes too.
- Follow-ups are replies in email 1's thread ("Re: …"), sent from the same mailbox, inside working
  hours, with per-mailbox daily caps (`EMAIL_MODE=mailbox`).
- One-click unsubscribe headers (Gmail and Yahoo require them from bulk senders).
- A plain-text copy of every email always goes too.

What you must set up (no code can do this):

1. **A separate sending domain** (e.g. `ozoo-mail.com` or `try.ozoo.ai`), never the main `ozoo.ai`.
2. **SPF, DKIM and DMARC** passing (Gmail → "Show original" shows PASS for all three).
3. **Warm-up** for 2-4 weeks: the mailbox caps start at 10 a day and rise to 30 by themselves.
4. **A real person as sender** (name and photo on the Google account), and replies answered.
5. **Your own tracking domain** for `LINK_BASE_URL` (e.g. `go.ozoo.ai`): links and images on
   `trycloudflare.com` look like phishing to filters. The live test uses a tunnel and will lean to
   Promotions or Spam for that reason alone.
6. **Clean lists:** verify addresses first (`VERIFY_MODE=real`); bounces above 2% hurt everything.
7. Watch **Google Postmaster Tools**: spam rate must stay under 0.3%.

If email 1 lands in Promotions on a warmed-up domain, the first thing to try is a version without
`[[image]]`: Duplicate it in the Templates screen, remove the picture, approve it, switch the
original off.
