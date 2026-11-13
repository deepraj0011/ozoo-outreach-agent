# Task 07: Email sequencer + templates

## Status
⏳ To do. Brief and rulings exist (`task-7-brief.md`, `task-7-rulings.md`). Not started.

## What it does (plain language)
Owns the 4-email sequence. It writes each lead's subject and body (with the right variant and tracked links) and enrolls the lead in the email tool's campaign. It later refreshes the next step when events change the variant. At sequence end it picks the next route: sales, WhatsApp, or a nurture track.

## Files
Planned: `src/outreach/sequencer.py`, `templates/emails.yaml`, `tests/test_sequencer.py`.

## Key functions and interfaces
Planned (plan plus rulings):
- `SCHEDULE_DAYS = {1:0, 2:4, 3:9, 4:15}`, `END_WAIT_DAYS = 5`, `VARIANT_PRIORITY`.
- `load_templates(path)`: validated and cached.
- `render_step(lead, step)` returns `subject_N` / `body_N`. Variant order: booking_slots, case_study, product, team_invite, standard. New subject only if the previous step was not opened.
- `render_reply(lead, key)`.
- `enroll(session, lead, tool, now)`: idempotent, campaign by timezone group, stores `tool_lead_id`.
- `refresh_next_step(session, lead, tool)`.
- `end_of_sequence_route(lead, human_opens, human_clicks) -> (track, actions)`: hot, then WhatsApp switch, then clicked, then opened, then silent.

## Depends on
Task 4 (`make_link`, `band`), Task 5 (actions, `SetVariant`, `RefreshNextStep`), Task 6 (`EmailTool`, `Deps.outreach.refresh_next_step`).

## Used by
Task 12 (`start_outreach` calls `enroll`; worker calls `end_of_sequence_route`). Task 6's `OutreachPort` gets its real `refresh_next_step` and `send_email_text` here and in Task 12.

## Key decisions (from the rulings)
- Template file layout is fixed; every body and reply has `{booking_link}` and `{unsubscribe_link}`.
- Lead name and company are sanitised (no CR/LF, 80 chars) to stop subject header injection.
- Missing campaign for a timezone group raises before any tool call.
- No template may say "opened", "clicked", "read your" or "saw you".

## Tests
Not started. Planned: template keys, no spying phrases, tracked links, variant priority, enroll once, one test per end-route branch, injection, idempotency, validation errors.

## Commits
None.

## Open items / deferred minors
Not started. Copy is placeholder and marked `# MARKETING: replace`.


---
## Update — built and reviewed (2026-11-13)
**Status:** ✅ Done, review clean. Commit `69b8fcc`. 24 tests (`tests/test_sequencer.py`).

**What was built:** `src/outreach/sequencer.py` + `templates/emails.yaml`.
- `load_templates()` validates the YAML at load: every step 1–4 has all variants, steps 2–4 have `new_subject`, every body has `{booking_link}` + `{unsubscribe_link}`, only allowed placeholders.
- `render_step(lead, step)` picks the body variant (`booking_slots > case_study > product > team_invite > standard`) and a new subject when the previous email wasn't opened. All links are signed tracked links (Task 4).
- `render_reply(lead, key)` renders the short reply emails (booking link, booking slots, reschedule).
- `enroll()` adds the lead to the right timezone campaign once (idempotent); `refresh_next_step()` re-renders the next email after any signal.
- `end_of_sequence_route()` decides what happens after Email 4: hot → sales personal email; never opened + WhatsApp opt-in → switch to WhatsApp; clicked → warm nurture; opened → monthly nurture; silent → slow nurture.

**Security:** lead name/company are cleaned (no line breaks/control chars, 80 chars) so they can't inject email headers; values are inserted once and never re-formatted; templates can't access attributes.

**Links:** uses Task 4 (links, score band), Task 5/6 (actions, email tool). Used by Task 12 (starter/worker calls `enroll`, `refresh_next_step`, `end_of_sequence_route`; `send_email_text` uses `render_reply`).

**Open items:** nested format-spec check; subject length cap is the same 80 chars as names (should be separate); Docker image must include `templates/`.
