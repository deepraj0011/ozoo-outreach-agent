# P0 Scheduled Emails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A lead that starts on email gets exactly the PM's scheduled templates on their days; tracking is recorded and shown, never acted on; unsubscribe / bounce / reply / booking cancel the rest.

**Architecture:** A new `src/outreach/p0/` package owns three tables (templates, schedule rows, each lead's planned emails) and the date maths. It plugs into the existing pipeline at five points: enrolment (`sequencer.enroll`), a new worker job that hands the due email to the existing own-sender tool (its `plan=1` + `due_at_<step>` mode, which already threads follow-ups and adds tracking), the tool's `sent` event (`rules._on_sent`), the `ToolStop` action (cancel the rest), and reply / click handling (`rules`, `classifier`). The old flow stays, selected by a setting.

**Tech Stack:** Python 3.10, FastAPI, SQLAlchemy 2 (`Mapped`), SQLite/Postgres via `create_all` (no migrations), pytest, plain JS + Tailwind CDN for the screens.

**Spec:** `docs/plan/p0-scheduled-emails-design.md` (§6 wins over §4).

## Global Constraints

- Python 3.10: `class X(str, Enum)`, no `StrEnum`; no new dependencies.
- P0 is on only when `settings.email_flow == "p0"` **and** the email tool has `SUPPORTS_SCHEDULE` (mailpit / gmail / mailbox). Default `email_flow = "classic"` so existing tests and Instantly mode are untouched. The user runs P0 with `EMAIL_FLOW=p0` in the environment (never edit `.env.local` / `.env.demo`).
- A P0 lead is marked by `lead.variant_flags["p0"] = True`; other P0 flags: `p0_day0` (ISO UTC), `p0_paused` (bool), `p0_clicks`, `p0_visits` (ints).
- Schedule: 1–10 emails; `day` integers 0–365, strictly increasing.
- Day N = calendar day N in `settings.business_tz` from Day 0's local date (Day 0 = the start time itself); with `time_scale != 1`: `day0 + N × 24 h / time_scale` (the send window is skipped then, as today).
- At most one P0 email per lead per business-tz calendar day (per scaled day under `time_scale`), except "Send next now".
- End: 5 days (scaled) after the last sent email → `ToolStop`, state `nurture`, reason `end_p0`, no alert.
- Placeholders = the existing `sequencer._values` keys (`first_name`, `company`, `signature`, `booking_link`, `homepage_link`, `product_link`, `voice_link`, `chat_link`, `pricing_link`, `case_study_link`, `trial_link`, `unsubscribe_link`) plus new `name` and `role`. (This replaces the design's `{link:<page>}` wording: the per-page `*_link` keys already exist and are tracked.) If a body has no `{unsubscribe_link}`, the renderer appends it on its own line.
- P0 emails render through `sequencer.render_version` with `rich = {"button": "Book a 30-minute demo"}` (the designed HTML part plus the text part).
- Routes are local only (under `/demo/`, registered only when `APP_ENV=local`); every POST must be `application/json`; no passcode; every change is written as a `system` `Event` with who did it.
- Logs: ids only — never emails, phones, message text or link tokens.
- Stage files explicitly by path; never `git add -A`. Commit messages `feat: …` / `test: …` / `docs: …`.

## Review Focus

1. A lead added at 23:30 IST on a Friday: email 1 goes in Monday's first slot; email 2 (Day 3) never goes the same day as email 1 → `test_one_email_per_day_after_window_shift` (Task 5).
2. The PM edits the schedule while email 2 is handed to the sender but not sent: that row is not rewritten and no second copy is planned → `test_replan_leaves_handed_row` (Task 3).
3. A template taken off the schedule but still on some lead's unsent row cannot be switched off → `test_cannot_switch_off_template_on_planned_rows` (Task 3).
4. A reply lands after email 3 was handed to the sender: the tool stop prevents the send and row 3 is `cancelled` → `test_reply_after_handoff_cancels_handed_row` (Task 6).
5. The worker ticks twice at the same `now`, and a lead is started twice: no duplicate rows, no second hand-off → `test_dispatch_is_idempotent` (Task 5), unique `(lead_id, email_no)`.

---

### Task 1: Models and P0 templates

**Files:**
- Create: `src/outreach/p0/__init__.py` (empty), `src/outreach/p0/templates.py`
- Modify: `src/outreach/models.py` (add the three models), `src/outreach/sequencer.py` (`_values`: add `name`, `role`)
- Test: `tests/test_p0_templates.py`

**Interfaces:**
- Produces:
  - `P0Template(id, name: String(120), subject: Text, body: Text, status: String(10) "draft|live|off", created_by, updated_by: String(80), created_at, updated_at)`, table `p0_template`
  - `P0ScheduleRow(id, email_no: int unique, template_id: FK p0_template.id, day: int)`, table `p0_schedule_row`
  - `PlannedEmail(id, lead_id: FK lead.id, email_no: int, template_id: FK, due_at: UTCDateTime, status: String(10) "planned|handed|sent|cancelled|skipped", tool_step: Optional[int], handed_at, sent_at: Optional[UTCDateTime], reason: Optional[String(50)], sent_subject: Optional[Text], sent_text: Optional[Text])`, table `planned_email`, `UniqueConstraint("lead_id", "email_no")`, `Index("ix_planned_email_status_due", "status", "due_at")`
  - in `p0/templates.py`: `class P0Error(ValueError)`; `PLACEHOLDERS: Tuple[str, ...]`
  - `list_templates(session) -> List[dict]` (id, name, subject, body, status, `used_by: List[int]` schedule email numbers, updated_at, updated_by)
  - `save_template(session, data: dict, by: str, now: datetime, *, settings) -> P0Template` (create without `id` → `draft` unless `status` given; edit keeps status)
  - `set_status(session, template_id: int, status: str, by: str, now: datetime) -> P0Template`
  - `render(lead, tpl: P0Template, email_no: int, *, settings) -> dict` → `{"subject", "text", "html"}` via `render_version(lead, {"key": f"p0.{tpl.id}", "subject": ..., "body": ..., "rich": {"button": "Book a 30-minute demo"}}, email_no, templates=load_templates(), settings=settings)`
  - `preview(session, settings, *, template_id: Optional[int] = None, draft: Optional[dict] = None, role: str = "Head of Collections") -> dict` (lead from `template_library.sample_lead`)
  - `ensure_starter(session, now: datetime, *, settings) -> None`: when `p0_template` is empty, insert the 4 standard emails (`sequencer.step_versions(load_templates(), step, "standard")[0]`, steps 1–4) as `live`, names from `template_library.STEP_LABELS`, `created_by="starter"`, then the schedule Day 0 / 4 / 9 / 15 (Task 2 `save_schedule`). Idempotent.

- [ ] **Step 1: Write failing tests** in `tests/test_p0_templates.py`:
  - `test_save_creates_draft_and_records_by` — `save_template(session, {"name": "Intro", "subject": "Hi {first_name}", "body": "Hello {first_name} at {company}"}, "pm", NOW, settings=s)` → `status == "draft"`, `created_by == "pm"`.
  - `test_unknown_placeholder_rejected` — body `"Hi {nickname}"` → `P0Error`, message contains `"{nickname}"`.
  - `test_blank_subject_or_body_rejected` → `P0Error`.
  - `test_render_fills_name_company_role_and_links` — lead `name="Asha Rao"`, `company="Acme Finance"`, `role="CTO"`, body `"{first_name} {name} {company} {role} {booking_link}"` → text contains `"Asha"`, `"Asha Rao"`, `"Acme Finance"`, `"CTO"`, `s.booking_url`-based link; `html != ""`.
  - `test_render_always_has_unsubscribe` — body without `{unsubscribe_link}` → text contains the lead's unsubscribe URL (`make_unsubscribe_url(lead.id, 1, settings=s)`).
  - `test_cannot_switch_off_template_on_schedule` — template on the schedule → `set_status(..., "off")` raises `P0Error` whose message contains `"email 1"`.
  - `test_ensure_starter_once` — empty DB → 4 live templates, schedule days `[0, 4, 9, 15]`; second call adds nothing.
- [ ] **Step 2:** `.venv/bin/pytest tests/test_p0_templates.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement. Placeholder check with `string.Formatter().parse` on subject and body; limits reuse `template_library.MAX_NAME / MAX_SUBJECT / MAX_BODY`. History: `Event(channel="system", kind="p0_template", data={"id", "action", "by"})`.
- [ ] **Step 4:** `.venv/bin/pytest tests/test_p0_templates.py tests/test_sequencer.py tests/test_templates.py -q` → PASS.
- [ ] **Step 5:** commit `feat: P0 template library with starter templates`.

### Task 2: The schedule

**Files:**
- Create: `src/outreach/p0/schedule.py`
- Test: `tests/test_p0_schedule.py`

**Interfaces:**
- Consumes: Task 1 models; Task 3 `due_at` and `replan_all` (import inside the function to avoid a cycle).
- Produces:
  - `get_schedule(session) -> List[dict]` `[{"email_no", "template_id", "template_name", "day"}]` by email_no
  - `save_schedule(session, rows: List[dict], by: str, now: datetime, *, settings) -> List[dict]` — validate, replace all rows (email_no = position + 1), `replan_all`, history event `p0_schedule`
  - `example_dates(session, now: datetime, *, settings) -> List[dict]` `[{"email_no", "day", "date": "YYYY-MM-DD"}]` (business-tz dates of `due_at(now, day)`)

- [ ] **Step 1: Failing tests:**
  - `test_save_and_read_back` — `[{"template_id": a, "day": 0}, {"template_id": b, "day": 3}]` → email_no 1, 2 with those days.
  - `test_days_must_increase` — days `[0, 3, 3]` → `P0Error` containing `"later day"`.
  - `test_one_to_ten_emails` — 0 rows and 11 rows → `P0Error`.
  - `test_template_must_be_live` — a draft template → `P0Error`.
  - `test_example_dates_for_today` — NOW, days `[0, 4]` → `["2026-11-12", "2026-11-16"]`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit `feat: P0 email schedule`.

### Task 3: Planner (dates, rows, cancel, mark sent, lead view)

**Files:**
- Create: `src/outreach/p0/planner.py`
- Modify: `src/outreach/p0/templates.py` (`set_status` also refuses `off` when an unsent row uses the template)
- Test: `tests/test_p0_planner.py`

**Interfaces:**
- Consumes: Task 1 models, `send_plan._scaled`.
- Produces:
  - `due_at(day0: datetime, day: int, *, settings) -> datetime` — `day == 0` → `day0`; scale 1 → local midnight of (`day0`'s business-tz date + `day`) in UTC; scaled → `day0 + _scaled(day, settings)`
  - `local_day(at: datetime, *, settings) -> str` — business-tz date ISO; under `time_scale` the scaled-day bucket `str(int((at - EPOCH) / (timedelta(days=1) / scale)))`
  - `plan_lead(session, lead, day0: datetime, *, settings) -> List[PlannedEmail]` — flags `p0=True`, `p0_day0`; one `planned` row per schedule row; no-op when rows exist
  - `replan_all(session, now: datetime, *, settings) -> int` — every `in_sequence` lead with flag `p0`: `planned` rows rewritten by email_no (template, `due_at` from `p0_day0`); schedule email whose template the lead already has on a `handed`/`sent` row → `skipped`, reason `already_sent`; email numbers gone from the schedule → `cancelled`, reason `removed`; new numbers → new rows; `handed`/`sent`/`cancelled`/`skipped` untouched; then `shift_overdue`. Returns leads changed.
  - `shift_overdue(rows: Sequence[PlannedEmail], now: datetime, *, settings) -> None` — if the first `planned` row's `due_at` is before the start of `local_day(now)`, move it and every later `planned` row by the same delta so the first is due at `now`
  - `next_row(session, lead) -> Optional[PlannedEmail]` — lowest email_no `planned`
  - `cancel_remaining(session, lead, reason: str, now: datetime) -> int` — `planned` and `handed` → `cancelled`
  - `mark_sent(session, lead, tool_step: int, now: datetime) -> Optional[PlannedEmail]` — the `handed` row with that `tool_step` → `sent`, `sent_at=now` (subject/text were copied at hand-off, Task 5)
  - `lead_view(session, lead, now: datetime, *, settings) -> List[dict]` — per row: email_no, template_name, status, `due_date`, sent_at, reason, sent_subject, `late`, `opens`, `clicks`, `visits` (from `Event` rows with this `lead_id`, `step == email_no`, `verdict == "human"`, kinds `open`/`click`/`visit`). `late` = `planned` and `due_at` before the start of today, or `handed` for more than 1 day (scaled) without `sent`.

- [ ] **Step 1: Failing tests** (NOW = 2026-11-12 10:00 UTC, a Thursday):
  - `test_day0_is_start_and_day_n_is_calendar_day` — `due_at(NOW, 0) == NOW`; `due_at(NOW, 3)` in Asia/Kolkata == 2026-11-15 00:00.
  - `test_late_evening_day1_is_next_calendar_day` — day0 = 2026-11-13 18:00 UTC (23:30 IST) → `local_day(due_at(day0, 1)) == "2026-11-14"`.
  - `test_scaled_days` — `time_scale=288` → `due_at(NOW, 1) == NOW + timedelta(minutes=5)`.
  - `test_plan_lead_once` — called twice → 4 rows.
  - `test_replan_rewrites_planned_by_number` — email 2 day 4→6 → row 2's local date moves 2 days; sent row 1 untouched.
  - `test_replan_leaves_handed_row` — row 2 `handed`; schedule changes email 2's template → row 2 unchanged; still one row with email_no 2.
  - `test_replan_skips_template_already_sent` — row 1 sent with template A; new schedule email 3 = A → row 3 `skipped`, reason `already_sent`.
  - `test_shift_overdue_keeps_gaps` — planned rows due NOW−3d and NOW → first due NOW, second NOW+3d.
  - `test_cancel_remaining` — planned + handed → cancelled; sent unchanged.
  - `test_mark_sent_flips_handed_row` — handed row `tool_step=2` → `sent`, `sent_at == NOW`.
  - `test_lead_view_late_and_counts` — a planned row due yesterday → `late is True`; one human click (step 1) and one `verdict="bot"` click → `clicks == 1` on row 1.
  - `test_cannot_switch_off_template_on_planned_rows` — template not on the schedule but on a `planned` row → `set_status(..., "off")` raises `P0Error`.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit `feat: P0 planner for each lead's planned emails`.

### Task 4: Own-sender tool sends up to 10 steps

**Files:**
- Modify: `src/outreach/emailtool/mailpit.py` (`_candidates`, `send_due`; mailbox / gmail inherit)
- Test: `tests/test_mailpit.py` (add)

**Interfaces:**
- Produces: tool variable `steps` (string int). With it, the lead is `done` after that step; without it, `LAST_STEP = 4` as today. New constant `MAX_STEPS = 10`.

- [ ] **Step 1: Failing tests:** `test_plan_lead_with_six_steps_sends_all_six` — `add_lead(..., {"plan": "1", "steps": "6"})`; for k in 1..6: `update_variables({f"subject_{k}", f"body_{k}", f"due_at_{k}": past ISO})` then `send_due` → 6 sends, status `done` after step 6, steps 2–6 subjects start with `"Re: "`. `test_steps_default_still_four`.
- [ ] **Step 2:** FAIL. **Step 3:** `_candidates` uses `last_step < MAX_STEPS`; in `send_due`, `last = int(variables.get("steps") or LAST_STEP)`; skip when `row["last_step"] >= last`; `status = "done" if step >= last else "active"`. **Step 4:** `.venv/bin/pytest tests/test_mailpit.py tests/test_gmail_tool.py tests/test_mailbox_tool.py -q` → PASS. **Step 5:** commit `feat: own-sender tool sends up to 10 planned steps`.

### Task 5: Start and send (enrol, dispatch job, sent event)

**Files:**
- Create: `src/outreach/p0/flow.py`
- Modify: `src/outreach/config.py` (`email_flow: Literal["classic", "p0"] = "classic"`), `src/outreach/sequencer.py` (`enroll` P0 branch; `refresh_next_step` returns early for P0 leads), `src/outreach/worker.py` (job `("p0_dispatch", _p0_dispatch)` right after `plan_schedule`; `_end_of_sequence` and `_stuck_leads` skip leads with flag `p0`), `src/outreach/rules.py` (`_on_sent` P0 branch), `src/outreach/actions.py` (action `MarkPlannedSent(step: int)`)
- Test: `tests/test_p0_flow.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `is_p0(settings, tool) -> bool`
  - `enroll` P0 branch (when `is_p0`): `ensure_starter`; `day0 = flags["plan_start"]` if present else `now`; `lead.tool_lead_id = tool.add_lead(campaign_id, lead, {"plan": "1", "steps": str(len(schedule)), "first_name": ..., "company": ...})`; `plan_lead(session, lead, day0, settings=s)`; flag `enrolled_at`. The classic body is skipped.
  - `dispatch_due(session, lead, tool, now, *, settings) -> bool` — return False when: flag `p0_paused`; state ≠ `in_sequence`; no `tool_lead_id`; any `handed` row; `next_row` is None or its `due_at > now`; or (row reason ≠ `send_now` and some row's `handed_at` has the same `local_day` as `now`). Otherwise render (Task 1), `k = count(handed or sent rows) + 1`, `tool.update_variables(tid, {f"subject_{k}", f"body_{k}", f"html_{k}", f"due_at_{k}": now.isoformat(), **sequencer._tracking_vars(lead, k, row.email_no, settings)})` plus `f"due_exact_{k}": "1"` for `send_now`; row → `handed`, `tool_step=k`, `handed_at=now`, `sent_subject`, `sent_text` — all in the caller's transaction. Returns True.
  - worker `_p0_dispatch(ctx, session) -> int` — `in_sequence` email leads with a `planned` row `due_at <= now`, filtered in `keep` by flag `p0`, via `_each_lead` + `dispatch_due`.
  - `rules._on_sent`: `if lead.flags.get("p0")` → `[SetStepSent(event.step), MarkPlannedSent(event.step)]`.
  - `actions.MarkPlannedSent` → `planner.mark_sent(session, lead, a.step, now)`.

- [ ] **Step 1: Failing tests** (tool = `MailpitEmailTool` with a temp state file and stub SMTP as in `tests/test_mailpit.py`; settings `email_flow="p0"`):
  - `test_new_lead_gets_rows_and_tool_waits` — start a lead → 4 planned rows; tool variables `plan == "1"`, `steps == "4"`, no `subject_1`.
  - `test_dispatch_hands_email1_then_sent_marks_row` — tick at NOW → row 1 `handed`, tool `subject_1` == rendered template 1 subject; `sent` event step 1 → row 1 `sent`, `lead.step_sent == 1`.
  - `test_one_email_per_day_after_window_shift` — schedule `[0, 1]`; row 1 handed Fri 23:30 IST, reported `sent` Mon 10:00 IST (send window). At Mon 10:30 IST row 2 is due but `dispatch_due` returns False; at Tue 10:00 IST it is handed. (The check uses the `local_day` of every row's `handed_at` **and** `sent_at`.)
  - `test_dispatch_is_idempotent` — two ticks at the same `now` → one hand-off; `start_outreach` twice → 4 rows.
  - `test_classic_flow_unchanged_when_setting_off` — `email_flow="classic"` → no `planned_email` rows; classic `subject_1..4` present.
  - `test_import_start_time_is_day0` — flag `plan_start` = NOW + 2 days → row 1 `due_at` == that time.
- [ ] **Step 2:** FAIL. **Step 3:** implement (one-per-day check uses `handed_at` and `sent_at` of every row). **Step 4:** `.venv/bin/pytest tests/test_p0_flow.py tests/test_rules.py tests/test_worker.py tests/test_sequencer.py -q` → PASS. **Step 5:** commit `feat: P0 leads start on the schedule and send through the own sender`.

### Task 6: Stops, replies, record-only tracking, end

**Files:**
- Modify: `src/outreach/actions.py` (on `ToolStop` for a lead with flag `p0`: also `planner.cancel_remaining(session, lead, reason, now)`, reason = the `SetState` reason in the same batch if any, else `"stopped"`), `src/outreach/rules.py` (`decide`: P0 record-only for `click`/`visit`; `_on_reply` P0 branch), `src/outreach/classifier.py` (`actions_for_class` P0 branch, placed before the domain-match check), `src/outreach/worker.py` (job `("p0_finish", _p0_finish)` before `end_of_sequence`)
- Test: `tests/test_p0_events.py`

**Interfaces:**
- Produces:
  - rules, P0 lead: `click` → `[SetFlag("p0_clicks", n + 1)]`, `visit` → `[SetFlag("p0_visits", n + 1)]` (no score, alert, variant or booking slots); `open` unchanged (already count-only).
  - rules `_on_reply`, P0 lead in `OUTREACH_STATES` → `[Classify()]`.
  - classifier, P0 lead in `in_sequence`: `ooo` → `[]`; `unsubscribe` → the existing branch; any other label → `[StopAllOutreach(), SetState(S.ENGAGED, f"reply_{label}"), Alert("engaged", f"lead {lead.id} replied ({label}); emails stopped, please answer personally")]`, never `SendReply`.
  - worker `_p0_finish(ctx, session) -> int`: P0 lead `in_sequence`, no `planned`/`handed` rows, and (latest `sent_at` ≤ now − `_scaled(5)` or nothing sent) → `execute([ToolStop(), SetState(S.NURTURE, "end_p0")])`.

- [ ] **Step 1: Failing tests:**
  - `test_unsubscribe_cancels_rest_and_blocks` — after email 1 sent: `unsubscribe` → rows 2–4 `cancelled`, lead `suppressed`, tool lead `stopped`.
  - `test_hard_bounce_cancels_rest` — rows cancelled; a lead with WhatsApp consent switches channel (existing behaviour).
  - `test_reply_interested_stops_and_alerts_no_auto_reply` — classifier result `interested` → rows cancelled, state `engaged`, one `engaged` alert, tool `reply` never called.
  - `test_ooo_reply_keeps_schedule` — `ooo` → rows unchanged, state `in_sequence`.
  - `test_reply_after_handoff_cancels_handed_row` — row 3 `handed`; reply classified `question` → row 3 `cancelled`; next `send_due` sends nothing.
  - `test_click_and_visit_change_nothing` — pricing click + verified visit → no `Alert` rows, score unchanged, rows unchanged, `p0_clicks == 1`, `p0_visits == 1`.
  - `test_whatsapp_reply_cancels_email_rows` — `wa_text` from the lead → rows cancelled.
  - `test_booking_cancels_rest` — `booking_created` → rows cancelled.
  - `test_finish_moves_to_nurture_without_alert` — last row sent 5 days ago → `nurture`, reason `end_p0`, no `Alert` row.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** `.venv/bin/pytest tests/test_p0_events.py tests/test_rules.py tests/test_classifier.py -q` → PASS. **Step 5:** commit `feat: P0 stops on unsubscribe, bounce, reply and booking; tracking is record-only`.

### Task 7: Per-lead controls

**Files:**
- Modify: `src/outreach/p0/flow.py`
- Test: `tests/test_p0_flow.py` (add)

**Interfaces:**
- Produces (each writes `Event(channel="system", kind="p0_control", data={"action", "by"})`):
  - `pause(session, lead, tool, by: str, now) -> None` — flag `p0_paused=True`; `tool.pause(tid)` if a row is `handed`
  - `resume(session, lead, tool, by: str, now, *, settings) -> None` — clear the flag, `shift_overdue`, `tool.resume(tid)`
  - `stop(session, lead, deps, by: str, now) -> None` — `execute(session, lead, [ToolStop(), SetState(S.NURTURE, "p0_stopped")], deps, now)`
  - `send_now(session, lead, tool, by: str, now, *, settings) -> bool` — next planned row: `due_at = now`, reason `send_now`; then `dispatch_due`
- [ ] **Step 1: Failing tests:** `test_pause_holds_and_resume_shifts`, `test_stop_cancels_and_nurtures`, `test_send_now_hands_next_even_same_day`.
- [ ] **Steps 2–4:** FAIL → implement → PASS. **Step 5:** commit `feat: P0 per-lead pause, resume, stop and send now`.

### Task 8: Screens and JSON routes

**Files:**
- Create: `src/outreach/p0/console.py`, `src/outreach/p0/console.js` (served at `/demo/p0.js`)
- Modify: `src/outreach/app.py` (register next to `schedule_console`, only when `app_env == "local"`), `src/outreach/demo_console.html` (when `/demo/api/p0/status` says on: the **Templates** and **Email schedule** nav items open the P0 screens; the lead panel gets an **Emails** tab)
- Test: `tests/test_p0_console.py`

**Interfaces:**
- Routes (POST needs `content-type: application/json`, else 415; `P0Error` → 400 `{"error": message}`):
  - `GET /demo/api/p0/status` → `{"on": bool}`
  - `GET /demo/api/p0/templates` → `{"templates": [...], "placeholders": [...]}`; `POST /demo/api/p0/templates` `{id?, name, subject, body, status?, by}`; `POST /demo/api/p0/templates/{id}/status` `{status, by}`
  - `POST /demo/api/p0/preview` `{template_id? | draft?, role?}` → `{subject, text, html}`
  - `GET /demo/api/p0/schedule` → `{"rows", "example"}`; `POST /demo/api/p0/schedule` `{rows, by}`
  - `GET /demo/api/p0/leads/{id}` → `{"rows": lead_view, "paused": bool}`; `POST /demo/api/p0/leads/{id}/{action}` (`pause|resume|stop|send_now`) `{by}`
- Screens (plain, Tailwind; replaced by the new frontend later): Templates list + editor with live preview and placeholder chips; Email schedule table `# | template ▾ | Day N`, Add / Remove, Example dates, Save; lead **Emails** tab: ✓ sent (date, opens / clicks), Next, to come, cancelled / skipped with reason, a "late" badge, the 4 buttons.
- [ ] **Step 1: Failing tests** (`TestClient(create_app(...))`, local settings, `email_flow="p0"`): `test_routes_absent_outside_local`, `test_post_requires_json`, `test_template_round_trip`, `test_schedule_save_error_is_400`, `test_lead_rows_and_pause`.
- [ ] **Steps 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** manual check in an isolated temp dir (own env file with `EMAIL_FLOW=p0`, `EMAIL_MODE=mailpit`, own DB, port 8011): add a lead → rows shown; Send now → arrives in Mailpit; browser console clean. Commit `feat: P0 Templates, Email schedule and lead Emails screens`.

### Task 9: End-to-end, docs, progress

**Files:**
- Create: `tests/test_p0_e2e.py`, `docs/guides/p0-email-schedule.md`, `docs/progress/tasks/task-28-p0-scheduled-emails.md`
- Modify: `docs/README.md` (guide row), `docs/progress/README.md` (status row + test count), `docs/progress/decisions.md` (P0/P1 split; record-only tracking; reply = stop + alert, no auto-reply; Day N = calendar day; one email per day; `EMAIL_FLOW`)
- [ ] **Step 1:** `test_p0_full_journey` — schedule Day 0 / 2 / 5 with 3 templates; add a lead; move the clock with ticks + `send_due` + sent events → three emails in order, email 1 with template 1's subject, emails 2–3 `"Re: <email 1 subject>"` with templates 2–3's bodies, never two on one day; a click in between changes nothing. `test_p0_journey_unsubscribe_mid_way` — stops after email 2.
- [ ] **Step 2:** run → PASS (fix only P0 code).
- [ ] **Step 3:** `.venv/bin/pytest -q` → all pass; note the count.
- [ ] **Step 4:** write the guide (Templates and Email schedule screens, what stops emails, how to run: `EMAIL_FLOW=p0 ENV_FILE=.env.demo scripts/run_local`), the task note, status row, decisions.
- [ ] **Step 5:** commit `docs: P0 scheduled emails guide and progress`.
