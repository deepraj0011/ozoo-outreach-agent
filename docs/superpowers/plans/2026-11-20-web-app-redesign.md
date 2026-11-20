# Web App Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the plain HTML/JS app at `/app` with a React + Material UI app in the dark graphite and teal look, with a clearer Campaign page and new Connectors and Settings pages.

**Architecture:** Backend gains one additive board field and two read-only, local-only JSON routes (`/demo/api/connectors`, `/demo/api/settings`) built by pure functions in a new `outreach.workspace` package. The frontend in `frontend/` (Vite + TS + MUI v6, already scaffolded) is restyled and extended, built into `src/outreach/webapp/`, and `web_app.py` serves that build with an SPA fallback. `src/outreach/web/` is deleted in the last task.

**Tech Stack:** FastAPI, SQLAlchemy, pytest (Python 3.10); React 18, MUI 6 + `@mui/icons-material`, react-router-dom 6, Vite 5, TypeScript 5, vitest for pure frontend helpers.

**Spec:** `docs/superpowers/specs/2026-11-20-web-app-redesign-design.md`

## Global Constraints

- Code runs on Python 3.10 (`class X(str, Enum)`, no `StrEnum`); full suite `.venv/bin/pytest -q` stays green after every task.
- Never touch `local.db*`, `demo.db*`, `.env.local`, `.env.demo`, `.gmail_tool.db`, `.mailpit_tool.db`. Smoke-test in a temp dir with its own env file and DB.
- Stage files explicitly by path, never `git add -A`. Before deleting anything under `src/outreach/web/`, run `git status src/outreach/web` and stop if another session has uncommitted edits there.
- No secret value (API keys, passwords, tokens, app passwords, webhook URLs, `*_secret`) may appear in any response, log or UI. Connectors list env key **names** only.
- Logs never contain emails, phone numbers, message text or tracked-link tokens.
- `/demo/api/*` and `/app` stay local only (registered inside the `settings.app_env == "local"` block of `app.py`).
- Dark theme only. Tokens: page `#121417`, surface `#1a1d21`, raised `#202328`, sidebar `#16181b`, divider `#2a2e34`, text `#e7e9ec` / `#9aa1ab`, primary `#2dd4bf` (contrast text `#0b1f1d`, tint `#1d2e2c`), success `#4ade80` on `#173322`, warning `#fbbf24` on `#3a2f12`, error `#f87171`. Radius 8px buttons/inputs, 12px cards, pill chips. Font Plus Jakarta Sans via `@fontsource/plus-jakarta-sans`.
- Icons only from `@mui/icons-material`, Rounded variants (`...Rounded`). Connector logos are local SVG files in `frontend/src/assets/logos/`; no runtime CDN.
- UI copy: short plain labels, no em dashes or en dashes.
- After the last task: update `docs/progress/README.md` (row + test count), add `docs/progress/tasks/task-31-web-app-redesign.md`, add a line to `docs/README.md` for the spec and plan.

## Review Focus

- **Deep link reload** (`/app/connectors` refreshed in the browser) must serve the app, not 404: test in Task 8.
- **Agent stopped / API unreachable**: every page shows an error alert with a Retry button, never a blank page: test via `pageState()` in Task 4.
- **Campaign not started** (`campaign.running == false`, every cell `planned`): header shows "Not started" and a primary "Start" button: test in Task 5 (`campaignHeaderState`).
- **Empty data** (no leads, or no schedule rows): Campaign shows an empty state with "Add leads"; schedule strip shows only the "Add email" tile: test in Task 5 (`stripItems([])`).
- **Secrets in config reach the browser**: a settings object filled with sentinel secrets yields responses that never contain the sentinel: tests in Tasks 2 and 3.

---

### Task 1: `sent_today` in the board totals

**Files:**
- Modify: `src/outreach/p0/board.py:126-135` (`board()` totals)
- Test: `tests/test_p0_board.py`

**Interfaces:**
- Produces: `board(...)["totals"]["sent_today"]: int`, the number of cells with `status == "sent"` whose sent time is at or after `planner._day_start(now, settings=settings)`. Existing keys unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/test_p0_board.py`, using the file's existing fixtures to create one lead with email 1 sent at `NOW - 1h` and another with email 1 sent at `NOW - 2 days`:

```python
def test_totals_count_emails_sent_today(...):
    out = board(session, NOW, settings=settings)
    assert out["totals"]["sent"] == 2
    assert out["totals"]["sent_today"] == 1

def test_totals_sent_today_zero_without_sends(...):
    assert board(session, NOW, settings=settings)["totals"]["sent_today"] == 0
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_p0_board.py -q -k sent_today`. Expected: FAIL with `KeyError: 'sent_today'`.
- [ ] **Step 3: Implement.** Add `"sent_today": 0` to the totals dict and increment it per sent cell whose sent time (the row's datetime, not the iso string) is `>= day_start`.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_p0_board.py -q`. Expected: all PASS.
- [ ] **Step 5: Commit** `git add src/outreach/p0/board.py tests/test_p0_board.py && git commit -m "feat: board totals count emails sent today"`

---

### Task 2: Connector status (pure)

**Files:**
- Create: `src/outreach/workspace/__init__.py` (empty), `src/outreach/workspace/connectors.py`
- Test: `tests/test_workspace_connectors.py`

**Interfaces:**
- Produces: `connectors(settings: Settings, session: Session, now: datetime) -> List[Dict[str, Any]]`. Each item:
  `{"id": str, "group": "email" | "calendar" | "messaging", "name": str, "account": str, "status": "active" | "attention" | "available" | "coming_soon", "detail": str, "basis": str, "setup": {"steps": List[str], "env_keys": List[str]}}`.
- Ids and order (fixed): `gmail`, `mailboxes`, `instantly` (group `email`); `cal`, `google_calendar`, `outlook_calendar` (group `calendar`); `whatsapp`, `teams`, `llm`, `verify` (group `messaging`).

Status rules (copy into the module docstring):
- Email tools: the one matching `settings.email_mode` (`gmail` → gmail, `mailbox` → mailboxes, `real` with `tool_name == "instantly"` → instantly) is `active`; the others `available`. `fake` / `mailpit` modes: no email card is active; the first card's detail says "Local test mode: emails go to Mailpit or memory".
- The active email tool is `attention` when an `Alert` with `kind in ("mailbox_paused", "mailbox_unhealthy")`, `resolved_at IS NULL` and `created_at >= now - 24h` exists.
- `cal`: `active` when an `Event` with `channel == "cal"` exists (detail "Last booking <d Mon>" from the latest `received_at`), else `available` with "No booking received yet".
- `google_calendar`, `outlook_calendar`: always `coming_soon`, setup steps `["In-app sign-in is coming in the next update."]`, `env_keys: []`.
- `whatsapp`, `teams`, `llm`, `verify`: `active` when their `*_mode` is `real`, `available` when unset, "Local test mode" detail when `fake`. `teams` is `attention` when any `Alert` with `kind != "integration_error"`, `posted_at IS NULL` and `created_at <= now - 10 min` exists.
- `basis` states what the status rests on, e.g. "No mailbox alerts in the last 24 h", "Based on settings only".
- `account`: non-secret identity only: Gmail address, mailbox count ("2 mailboxes"), `llm_provider` name, WhatsApp "Phone number set" (never the id), Teams "Sales channel set". Never a key, token, password, URL or secret.
- `env_keys` per card: upper-case names of the real fields in `config.py` (e.g. `EMAIL_MODE`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `MAILBOXES`, `TOOL_NAME`, `TOOL_API_KEY`, `CAL_WEBHOOK_SECRET`, `WHATSAPP_MODE`, `WA_PHONE_NUMBER_ID`, `WA_ACCESS_TOKEN`, `WA_VERIFY_TOKEN`, `ALERTS_MODE`, `TEAMS_WEBHOOK_URL`, `LLM_MODE`, `LLM_PROVIDER`, `LLM_CLASSIFIER_MODEL`, `LLM_CHAT_MODEL`, `VERIFY_MODE`, `VERIFY_PROVIDER`, `ZEROBOUNCE_API_KEY`, `REACHER_URL`); a test asserts each listed key, lower-cased, is a field of `Settings`.

- [ ] **Step 1: Write the failing tests** (use `make_settings` from `tests/fakes.py`, which takes field overrides, and the `session` fixture):

```python
def test_order_and_groups(session):
    ids = [c["id"] for c in connectors(make_settings(), session, NOW)]
    assert ids == ["gmail", "mailboxes", "instantly", "cal", "google_calendar", "outlook_calendar",
                   "whatsapp", "teams", "llm", "verify"]

def test_gmail_mode_marks_only_gmail_active(session):
    by = {c["id"]: c for c in connectors(make_settings(app_env="local", email_mode="gmail",
                                                       gmail_address="a@ozoo.ai", gmail_app_password="x" * 16),
                                         session, NOW)}
    assert by["gmail"]["status"] == "active" and by["gmail"]["account"] == "a@ozoo.ai"
    assert by["mailboxes"]["status"] == "available" and by["instantly"]["status"] == "available"

def test_recent_mailbox_alert_needs_attention(session): ...   # Alert(kind="mailbox_unhealthy", text="x", created_at=NOW - 1h) → active tool "attention"
def test_old_or_resolved_mailbox_alert_ignored(session): ...  # created 25 h ago, or resolved_at set → "active"
def test_cal_active_after_a_booking_event(session): ...       # Event(channel="cal", kind="booked", occurred_at/received_at=NOW-2d) → "active", "Last booking" in detail
def test_calendars_are_coming_soon(session): ...
def test_teams_attention_on_stuck_alert(session): ...         # alerts_mode="real", Alert(kind="reply", posted_at=None, created_at=NOW-15min)
def test_env_keys_are_real_settings_fields(session): ...      # every key.lower() in Settings.model_fields

def test_no_secret_reaches_the_payload(session):
    secret = "SENTINEL-SECRET-91f3"
    s = make_settings(gmail_app_password=secret, tool_api_key=secret, wa_access_token=secret,
                      cal_webhook_secret=secret, zerobounce_api_key=secret)
    assert secret not in json.dumps(connectors(s, session, NOW))
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_workspace_connectors.py -q`. Expected: FAIL (`ModuleNotFoundError: outreach.workspace`).
- [ ] **Step 3: Implement** `connectors()` in `src/outreach/workspace/connectors.py`: one small builder function per card (each under 30 lines), plus `_recent_alerts(session, kinds, since)` and `_last_cal_event(session)`.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_workspace_connectors.py -q`. Expected: all PASS.
- [ ] **Step 5: Commit** `git add src/outreach/workspace/__init__.py src/outreach/workspace/connectors.py tests/test_workspace_connectors.py && git commit -m "feat: connector status for the web app"`

---

### Task 3: Settings view and the two routes

**Files:**
- Create: `src/outreach/workspace/settings_view.py`, `src/outreach/workspace/routes.py`
- Modify: `src/outreach/app.py:411-422` (register the routes in the local block, after `p0_console.register`)
- Test: `tests/test_workspace_settings.py`, `tests/test_workspace_routes.py`

**Interfaces:**
- Consumes: `connectors(settings, session, now)` (Task 2).
- Produces:
  - `settings_view(settings: Settings) -> Dict[str, Any]` returning `{"sections": [{"id": str, "title": str, "rows": [{"label": str, "help": str, "value": str | bool | int | List[str]}]}]}`, sections in order `sending`, `safety`, `alerts`, `company`, `team`, omitting any section with no rows.
  - `register(app: FastAPI, session_factory: sessionmaker, clock: Callable[[], datetime], settings: Any) -> None` adding `GET /demo/api/connectors` → `{"connectors": [...]}` and `GET /demo/api/settings` → `settings_view(...)`, both with `Cache-Control: no-store`.

Rows (read each field name from `config.py`):
- sending: "Working days" and "Working hours" (from `business_hours` via `hours.parse_business_hours`; days as `["Mon", ...]`, hours as `"09:00 to 18:00"`), "Daily limit per mailbox" (each mailbox's `daily_limit` joined, e.g. `"30, 30"`; omitted with no mailboxes), "Main channel when both are allowed" (`primary_when_both`).
- safety: "Only alert sales after a real visit" (`click_alerts_need_check`), "Confirm clicks with a script check" (`click_js_confirm`).
- alerts: "Alerts go to" (`alerts_mode`: `"Microsoft Teams"` when real, `"Console (local test)"` when fake).
- company / team: only rows backed by real non-secret fields (e.g. sender name `gmail_from_name`; number of entries in `teams_allowed_aad_ids` as `"3 people"`, never the ids).

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_workspace_settings.py
def test_sending_section_reads_business_hours():
    view = settings_view(make_settings(business_hours="Mon-Fri 09:30-17:30"))
    rows = {r["label"]: r["value"] for r in view["sections"][0]["rows"]}
    assert view["sections"][0]["id"] == "sending"
    assert rows["Working days"] == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert rows["Working hours"] == "09:30 to 17:30"

def test_settings_view_has_no_secret():
    secret = "SENTINEL-SECRET-91f3"
    s = make_settings(gmail_app_password=secret, cal_webhook_secret=secret, tool_api_key=secret)
    assert secret not in json.dumps(settings_view(s))

# tests/test_workspace_routes.py  (build the client like tests/test_web_app.py::build)
def test_connectors_route_lists_ten_cards():
    r = build().get("/demo/api/connectors")
    assert r.status_code == 200 and len(r.json()["connectors"]) == 10
    assert r.headers["cache-control"] == "no-store"

def test_settings_route_returns_sections():
    assert build().get("/demo/api/settings").json()["sections"][0]["id"] == "sending"

def test_routes_absent_outside_local():
    c = build(app_env="production")
    assert c.get("/demo/api/connectors").status_code == 404
    assert c.get("/demo/api/settings").status_code == 404
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_workspace_settings.py tests/test_workspace_routes.py -q`. Expected: FAIL (import errors).
- [ ] **Step 3: Implement** `settings_view`, `register`, and the one-line registration in `app.py`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: whole suite PASS.
- [ ] **Step 5: Commit** `git add src/outreach/workspace/settings_view.py src/outreach/workspace/routes.py src/outreach/app.py tests/test_workspace_settings.py tests/test_workspace_routes.py && git commit -m "feat: read-only connectors and settings routes"`

---

### Task 4: Theme, shell, API client and frontend test runner

**Files:**
- Modify: `frontend/package.json`, `frontend/src/theme.ts`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/api.ts`, `frontend/src/types.ts`
- Create: `frontend/src/components/PageState.tsx`, `frontend/src/lib/pageState.ts`, `frontend/src/lib/pageState.test.ts`, `frontend/src/pages/ConversationsPage.tsx`
- `App.tsx` imports a `LeadsPage` that does not exist yet: route `/leads` to a placeholder until Task 6.

**Interfaces:**
- Produces:
  - `theme` (MUI `createTheme`, `palette.mode: "dark"`, tokens from Global Constraints; defaults: `MuiButton` disableElevation + radius 8, `MuiCard`/`MuiPaper` outlined with divider border + radius 12, `MuiChip` pill, `MuiTableCell` head colour `text.secondary` weight 600, `MuiDrawer` paper `#1a1d21`).
  - `NAV: { to: string; label: string; icon: ReactNode; group: "main" | "workspace" }[]` in `App.tsx`: Campaign `/campaign` (`CampaignRounded`), Leads `/leads` (`GroupRounded`), Conversations `/conversations` (`ForumRounded`), Templates `/templates` (`DescriptionRounded`) in `main`; Connectors `/connectors` (`ExtensionRounded`), Settings `/settings` (`SettingsRounded`) under a "Workspace" label. Sidebar `#16181b`, selected item tint `#20302f` with text `#5eead4`. Logo tile "O" on primary + "Ozoo Outreach".
  - `type Loadable<T> = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; data: T }` and `pageState<T>(loading: boolean, error: unknown, data: T | null): Loadable<T>` in `lib/pageState.ts` (error message via existing `errorText`).
  - `<PageState state={Loadable<T>} onRetry={() => void} skeleton={ReactNode}>{(data: T) => ReactNode}</PageState>` rendering skeleton / `Alert severity="error"` with a "Retry" button / children.
  - `api.ts` additions (paths exactly as `src/outreach/web/js/api.js` uses them): `getBoard(): Promise<Board>`, `startCampaign(by: string)`, `pauseCampaign(by: string)`, `getConnectors(): Promise<{ connectors: Connector[] }>`, `getSettings(): Promise<SettingsView>`, `addLead(lead: NewLead)` → `POST /demo/api/intake`, `importLeads(file: File, commit: boolean)` → `POST /demo/api/import[?commit=1]` with `X-Filename`, `SAMPLE_CSV_URL = "/demo/api/sample.csv"`.
  - `types.ts` additions: `Board.campaign: { running: boolean; started_at: string | null; leads: number }`, `Totals.sent_today: number`, `ScheduleRow.mode: "now" | "day" | "at"`, `ScheduleRow.at: string | null`, `Connector`, `ConnectorStatus`, `SettingsView` matching Tasks 2 and 3.
  - `ConversationsPage`: empty state "WhatsApp conversations are in the live console for now" with a button opening `/demo` in a new tab.

- [ ] **Step 1:** In `frontend/`, run `npm install -E @fontsource/plus-jakarta-sans && npm install -D -E vitest@2 && npm uninstall @fontsource/inter`, and add `"test": "vitest run"` to `scripts`. Expected: exact versions pinned in `package.json`.
- [ ] **Step 2: Write the failing test** `frontend/src/lib/pageState.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { pageState } from "./pageState";
import { ApiError } from "../api";

describe("pageState", () => {
  it("is loading before data or error", () => expect(pageState(true, null, null)).toEqual({ status: "loading" }));
  it("shows the API message when the agent is down", () =>
    expect(pageState(false, new ApiError("Cannot reach the agent. Is it running?"), null))
      .toEqual({ status: "error", message: "Cannot reach the agent. Is it running?" }));
  it("is ready with data", () => expect(pageState(false, null, { a: 1 })).toEqual({ status: "ready", data: { a: 1 } }));
});
```

- [ ] **Step 3: Run** `cd frontend && npm test`. Expected: FAIL (module not found).
- [ ] **Step 4: Implement** `pageState`, `PageState`, theme, fonts in `main.tsx` (weights 400/500/600/700), shell, API and types, `ConversationsPage`. `/connectors` and `/settings` routes render a placeholder until Task 7.
- [ ] **Step 5: Run** `npm test && npm run build`. Expected: tests PASS, `tsc -b` clean, `src/outreach/webapp/index.html` written.
- [ ] **Step 6: Commit** `git add frontend/package.json frontend/package-lock.json frontend/src && git commit -m "feat: dark teal theme, app shell and API client for the MUI web app"`

---

### Task 5: Campaign page

**Files:**
- Modify: `frontend/src/pages/CampaignPage.tsx`, `frontend/src/components/ScheduleStrip.tsx`, `frontend/src/components/TotalsRow.tsx`, `frontend/src/components/BoardTable.tsx`, `frontend/src/components/EmailCell.tsx`, `frontend/src/components/status.ts`
- Create: `frontend/src/components/ScheduleDrawer.tsx`, `frontend/src/lib/campaign.ts`, `frontend/src/lib/campaign.test.ts`

**Interfaces:**
- Consumes: `getBoard`, `startCampaign`, `pauseCampaign`, `Board`, `PageState` (Task 4); existing `LeadDrawer`, `EmailDrawer`; schedule calls (`GET/POST /demo/api/p0/schedule`, `POST /demo/api/p0/schedule/example`).
- Produces (in `lib/campaign.ts`):
  - `timingLabel(row: ScheduleRow, tz: string): string`: `"now"` → `"Right away"`, `"day"` → `` `Day ${row.day}` ``, `"at"` → e.g. `"Tue 24 Nov, 10:00"` (`Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: tz })` parts, comma only between date and time).
  - `stripItems(rows: ScheduleRow[]): { emailNo: number; name: string; row: ScheduleRow }[]` sorted by `email_no`.
  - `campaignHeaderState(c: Board["campaign"]): { label: "Sending" | "Paused" | "Not started"; action: "pause" | "start" }`: running → Sending/pause; `started_at` set and not running → Paused/start; else Not started/start.
  - `cellLook(status: CellStatus): { icon: "sent" | "today" | "planned" | "cancelled"; tone: "success" | "warning" | "muted" }`: sent → sent/success, today → today/warning, planned and handed → planned/muted, cancelled and skipped → cancelled/muted.
  - `tabCounts(leads: BoardLead[]): { all: number; today: number; replied: number; stopped: number }` (today: any cell `today`; replied: `stop_reason == "replied"`; stopped: `stop_reason != null`).

Layout (mockup C, `.superpowers/brainstorm/397011-1795178815/content/campaign-look.html`): header row (h5 "Campaign", state chip with `PlayCircleRounded` / `PauseCircleRounded`, spacer, outlined "Pause all" or contained "Start" by `campaignHeaderState`, contained primary "Add leads" with `PersonAddRounded` linking to `/leads`); 4 tiles (`GroupRounded` "Leads in sequence" = `totals.leads - totals.stopped`; `SendRounded` "Sent today" = `` `${sent_today} of ${sent_today + today}` ``; `MarkEmailReadRounded` "Replies"; `BlockRounded` "Stopped or unsubscribed" = `totals.stopped`); schedule card (title "Email schedule", subtitle `` `${n} emails, sent in working hours` ``, text button "Edit" opening `ScheduleDrawer` with the existing template/timing/day/date controls and example dates; numbered dots joined by a line with template name and `timingLabel` under each, first dot filled; dashed "Add email" tile opens the drawer with a new row); lead table card (MUI `Tabs` All / Sending today / Replied / Stopped with counts, search field "Name or company" filtering name+company case-insensitively; columns Lead (initials `Avatar`, name, company; ellipsis + `Tooltip` past 28 characters), Status `Chip`, one column per schedule row headed "Email N", cells per `cellLook` with `CheckCircleRounded` / `ScheduleSendRounded` / no icon / strikethrough date). Row click opens `LeadDrawer`; cell click opens `EmailDrawer`. No leads: empty state "No leads yet" with "Add leads".

- [ ] **Step 1: Write the failing tests** `frontend/src/lib/campaign.test.ts`:

```ts
const row = (o: Partial<ScheduleRow>): ScheduleRow => ({ email_no: 1, mode: "day", day: 0, at: null, template_id: 1, template_name: "First note", ...o });
it("labels timings", () => {
  expect(timingLabel(row({ mode: "now" }), "Asia/Kolkata")).toBe("Right away");
  expect(timingLabel(row({ mode: "day", day: 9 }), "Asia/Kolkata")).toBe("Day 9");
  expect(timingLabel(row({ mode: "at", at: "2026-11-24T04:30:00Z" }), "Asia/Kolkata")).toBe("Tue 24 Nov, 10:00");
});
it("shows Not started with a Start action before the first start", () =>
  expect(campaignHeaderState({ running: false, started_at: null, leads: 3 })).toEqual({ label: "Not started", action: "start" }));
it("shows Paused after a start", () =>
  expect(campaignHeaderState({ running: false, started_at: "2026-11-20T10:00:00Z", leads: 3 })).toEqual({ label: "Paused", action: "start" }));
it("has no steps for an empty schedule", () => expect(stripItems([])).toEqual([]));
it("sorts steps by email number", () => expect(stripItems([row({ email_no: 2 }), row({ email_no: 1 })]).map((s) => s.emailNo)).toEqual([1, 2]));
it("maps cell statuses", () => {
  expect(cellLook("handed")).toEqual({ icon: "planned", tone: "muted" });
  expect(cellLook("skipped")).toEqual({ icon: "cancelled", tone: "muted" });
});
it("counts tabs", () => { /* 3 leads: one stop_reason "replied", one with a "today" cell, one plain → { all: 3, today: 1, replied: 1, stopped: 1 } */ });
```

- [ ] **Step 2: Run** `npm test`. Expected: FAIL (missing exports).
- [ ] **Step 3: Implement** `lib/campaign.ts`, then the components and page per the layout above.
- [ ] **Step 4: Run** `npm test && npm run build`. Expected: PASS, clean build.
- [ ] **Step 5: Smoke test** in an isolated temp dir: `TMP=$(mktemp -d)`; copy `.env.demo` to `$TMP/.env` and change only `DATABASE_URL` to `sqlite:///$TMP/t.db` (and any `*_STATE_FILE` / tool db paths to `$TMP`); start `.venv/bin/uvicorn --factory outreach.app:create_app --env-file $TMP/.env --port 8010`; seed with `.venv/bin/python -m outreach.dev --env-file $TMP/.env seed`; open `http://127.0.0.1:8010/app/campaign`. Expected: header, 4 tiles, strip, table render; Start/Pause works. Stop the server, `rm -rf $TMP`.
- [ ] **Step 6: Commit** `git add frontend/src && git commit -m "feat: Campaign page in the MUI web app"`

---

### Task 6: Templates and Leads pages

**Files:**
- Modify: `frontend/src/pages/TemplatesPage.tsx`, `frontend/src/components/TemplateEditor.tsx`, `frontend/src/components/EmailPreview.tsx`, `frontend/src/App.tsx` (route `/leads`)
- Create: `frontend/src/pages/LeadsPage.tsx`, `frontend/src/components/ImportDialog.tsx`, `frontend/src/lib/leads.ts`, `frontend/src/lib/leads.test.ts`

**Interfaces:**
- Consumes: `getBoard`, `addLead`, `importLeads`, `SAMPLE_CSV_URL`, template calls in `api.ts`, `PageState`.
- Produces: `validateNewLead(input: NewLead): Record<string, string>` in `lib/leads.ts`, returning field → message for: name required ("Add a name."), company required ("Add a company."), email containing `@` and a dot after it when given ("This email does not look right."), and at least one of email or phone (message on `email`: "Add an email or a phone number."). `NewLead` uses the field names `src/outreach/web/js/pages/leads.js` posts to `/demo/api/intake` (read them from that file).

Templates: two panes (list on the left with status chips draft / live / off and "Used by email N"; editor on the right with subject, body, placeholder chips, preview card). Same actions as today (save, set status, preview). Leads: table of board leads (name, company, role, status chip), "Add lead" dialog (labels above inputs, inline errors from `validateNewLead`, WhatsApp opt-in checkbox as in `leads.js`), "Import" dialog (pick file, preview table from `importLeads(file, false)`, "Import N leads" button calls `importLeads(file, true)`, link to the sample CSV).

- [ ] **Step 1: Write the failing tests** in `lib/leads.test.ts`:

```ts
it("needs name and company", () => expect(Object.keys(validateNewLead({ name: "", company: "", email: "a@b.in" })).sort()).toEqual(["company", "name"]));
it("rejects a malformed email", () => expect(validateNewLead({ name: "N", company: "C", email: "nope" }).email).toBe("This email does not look right."));
it("needs a way to reach the lead", () => expect(validateNewLead({ name: "N", company: "C" }).email).toBe("Add an email or a phone number."));
it("accepts a phone-only lead", () => expect(validateNewLead({ name: "N", company: "C", phone: "+919800000000" })).toEqual({}));
```

- [ ] **Step 2: Run** `npm test`. Expected: FAIL.
- [ ] **Step 3: Implement** `validateNewLead`, `LeadsPage`, `ImportDialog`, restyled Templates page.
- [ ] **Step 4: Run** `npm test && npm run build`. Expected: PASS.
- [ ] **Step 5: Smoke test** as in Task 5 Step 5: add one lead, import the sample CSV, edit and preview a template.
- [ ] **Step 6: Commit** `git add frontend/src && git commit -m "feat: Templates and Leads pages in the MUI web app"`

---

### Task 7: Connectors and Settings pages

**Files:**
- Create: `frontend/src/pages/ConnectorsPage.tsx`, `frontend/src/components/ConnectorCard.tsx`, `frontend/src/components/ConnectorDrawer.tsx`, `frontend/src/pages/SettingsPage.tsx`, `frontend/src/lib/connectors.ts`, `frontend/src/lib/connectors.test.ts`, `frontend/src/assets/logos/{gmail,googlecalendar,caldotcom,whatsapp,anthropic}.svg`
- Modify: `frontend/src/App.tsx` (routes)

**Interfaces:**
- Consumes: `getConnectors`, `getSettings`, `Connector`, `ConnectorStatus`, `SettingsView`, `PageState`.
- Produces (in `lib/connectors.ts`):
  - `summarize(cs: Connector[]): { active: number; attention: number; off: number }`, `off` = `available` + `coming_soon`.
  - `statusLook(s: ConnectorStatus): { label: string; tone: "success" | "warning" | "muted" }`: active → "Active"/success, attention → "Needs a look"/warning, available → "Not in use"/muted, coming_soon → "Coming soon"/muted.
  - `actionLabel(s: ConnectorStatus): "Manage" | "Fix" | "Set up" | "Coming soon"` (active, attention, available, coming_soon).
  - `GROUPS: { id: "email" | "calendar" | "messaging"; title: string; blurb: string }[]`: "Sending email" / "Where the emails go out from and where replies are read."; "Calendar and booking" / "Where leads book a demo and where demos land."; "Messaging, alerts and AI" / "WhatsApp to leads, alerts to the sales team, the model that reads replies."

Connectors page (mockup `connectors-settings.html`): title + "The tools the agent sends, books and alerts with."; summary chips (`CheckCircleRounded` active, `ErrorRounded` needs a look, `LinkOffRounded` not in use); one section per group, 3-column grid (1 column under 900px) of `ConnectorCard` (logo tile, or icon tile `DnsRounded` mailboxes / `RocketLaunchRounded` Instantly / `CalendarMonthRounded` Outlook / `GroupsRounded` Teams / `VerifiedRounded` verification; name, account, status chip, detail, footer with `basis` and the action button). The action opens `ConnectorDrawer` (right, 380px): vertical `Stepper` from `setup.steps`, `env_keys` in monospace each with a copy button, closing note "Restart the agent to apply changes." Coming-soon cards have a disabled button. Settings page: left section list (`ScheduleSendRounded` sending, `ShieldRounded` safety, `NotificationsRounded` alerts, `BusinessRounded` company, `GroupRounded` team, only for sections present), right card per section with one row per setting (label + help left; value right: day chips for lists, disabled `Switch` for booleans, text otherwise) and an info `Alert`: "These come from the agent's .env file. Change them there, then restart the agent."

- [ ] **Step 1: Write the failing tests** in `lib/connectors.test.ts`:

```ts
const c = (status: ConnectorStatus): Connector => ({ id: status, group: "email", name: "", account: "", status, detail: "", basis: "", setup: { steps: [], env_keys: [] } });
it("summarizes", () => expect(summarize([c("active"), c("attention"), c("available"), c("coming_soon")])).toEqual({ active: 1, attention: 1, off: 2 }));
it("labels statuses", () => expect(statusLook("available")).toEqual({ label: "Not in use", tone: "muted" }));
it("picks the action", () => { expect(actionLabel("attention")).toBe("Fix"); expect(actionLabel("available")).toBe("Set up"); });
```

- [ ] **Step 2: Run** `npm test`. Expected: FAIL.
- [ ] **Step 3: Implement** helpers, logos (SVG marks copied from the `simple-icons` package into `assets/logos/` at dev time; not a runtime dependency), pages and routes.
- [ ] **Step 4: Run** `npm test && npm run build`. Expected: PASS.
- [ ] **Step 5: Smoke test** as in Task 5 Step 5: `/app/connectors` shows 10 cards in 3 groups with statuses matching the temp env; drawer opens and copies a key name; `/app/settings` shows Sending first.
- [ ] **Step 6: Commit** `git add frontend/src && git commit -m "feat: Connectors and Settings pages"`

---

### Task 8: Serve the build at /app, build in run_local, retire the plain app, docs

**Files:**
- Modify: `src/outreach/web_app.py`, `src/outreach/app.py` (pass-through of an optional `web_dir`), `tests/test_web_app.py`, `scripts/run_local`, `CLAUDE.md` ("Local web app" paragraph), `docs/README.md`, `docs/progress/README.md`
- Create: `scripts/build_web`, `docs/progress/tasks/task-31-web-app-redesign.md`
- Delete: `src/outreach/web/` (only if `git status --porcelain src/outreach/web` is empty)

**Interfaces:**
- Produces: `register(app: FastAPI, web_dir: Path = WEB_DIR) -> None`, `WEB_DIR = Path(__file__).with_name("webapp")`. When `web_dir / "index.html"` exists: files under `/app/` served from `web_dir` with `Cache-Control: no-cache`; any other `GET /app/<path>` that is not a file returns `index.html` (SPA fallback); `/app` redirects to `/app/`. When it is missing: `GET /app/...` returns 503 with an HTML page "The web app is not built yet. Run scripts/build_web, then reload."
- `create_app(..., web_dir: Optional[Path] = None)` passes `web_dir or web_app.WEB_DIR` to `web_app.register`.
- `scripts/build_web`: `cd frontend && npm ci && npm run build`; exits 1 with "npm is not installed: install Node 20+ to build the web app." when `npm` is missing.
- `scripts/run_local`: before starting uvicorn, if `npm` exists and (`src/outreach/webapp/index.html` is missing or any file under `frontend/src` is newer than it) run `scripts/build_web`; without `npm`, print one warning line and continue.

- [ ] **Step 1: Write the failing tests** in `tests/test_web_app.py` (`build(app_env="local", web_dir=None)` passes `web_dir` to `create_app`); replace the plain-app index assertion, keep `test_app_without_slash_redirects_to_slash` and `test_root_redirects_to_app`:

```python
def test_serves_built_index(tmp_path):
    (tmp_path / "index.html").write_text('<div id="root"></div>')
    assert '<div id="root">' in build(web_dir=tmp_path).get("/app/").text

def test_deep_link_falls_back_to_index(tmp_path):
    (tmp_path / "index.html").write_text('<div id="root"></div>')
    r = build(web_dir=tmp_path).get("/app/connectors")
    assert r.status_code == 200 and '<div id="root">' in r.text

def test_missing_build_explains_itself(tmp_path):
    r = build(web_dir=tmp_path).get("/app/")
    assert r.status_code == 503 and "scripts/build_web" in r.text

def test_app_not_served_outside_local(tmp_path):
    (tmp_path / "index.html").write_text("x")
    assert build(app_env="production", web_dir=tmp_path).get("/app/").status_code == 404
```

- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_web_app.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `web_app.register`, the `create_app` pass-through, `scripts/build_web` (`chmod +x`), the `run_local` change.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: whole suite PASS.
- [ ] **Step 5: Retire the plain app.** `git status --porcelain src/outreach/web`: if empty, `git rm -r -q src/outreach/web`; if not, stop and report to the user. Then `grep -rn "outreach/web/\|web/js" src tests scripts docs CLAUDE.md` and fix any remaining reference.
- [ ] **Step 6: Final smoke test** in an isolated temp dir (as Task 5 Step 5) after `scripts/build_web`: visit `/app/`, `/app/campaign`, `/app/leads`, `/app/templates`, `/app/conversations`, `/app/connectors`, `/app/settings`, and reload `/app/connectors`. Expected: every page renders; reload works.
- [ ] **Step 7: Docs.** CLAUDE.md "Local web app": React + MUI in `frontend/`, built by `scripts/build_web` into `src/outreach/webapp/` (gitignored), served at `/app` locally, `cd frontend && npm test` for frontend helpers. Progress board row and test count from `.venv/bin/pytest -q`; task note (what changed; decisions: replaced the plain app, status-first connectors, read-only settings); `docs/README.md` lines for the spec and this plan.
- [ ] **Step 8: Commit**, staging each path explicitly: `git add src/outreach/web_app.py src/outreach/app.py tests/test_web_app.py scripts/build_web scripts/run_local CLAUDE.md docs/README.md docs/progress/README.md docs/progress/tasks/task-31-web-app-redesign.md docs/superpowers/plans/2026-11-20-web-app-redesign.md` (plus the `git rm` from Step 5), then `git commit -m "feat: serve the MUI web app at /app and retire the plain HTML app"`.
