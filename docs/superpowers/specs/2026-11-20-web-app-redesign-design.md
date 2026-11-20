# Web app redesign: Material UI, dark teal, Connectors and Settings

Date: 2026-11-20. Status: approved by the user 2026-11-20.

## Intent and why

The PM/sales user runs the outreach agent from the local web app at `/app`. The current screens
(plain HTML/JS in `src/outreach/web/`) were judged "too ugly and congested, not clearly visible,
random complex text in one column" (screenshot 2026-11-20 16:08). The user wants a real product
UI: Material UI, good icons, clean structure, clear colours on buttons, and two new areas,
**Connectors** (calendar, Gmail and the other tools) and **Settings**.

Success: a person opening `/app` sees at a glance what the campaign is doing, every lead's
progress across the emails, and whether each connected tool works, without reading dense text.

## Decisions made with the user

| Decision | Choice | Source |
|---|---|---|
| Component library | Material UI (MUI v6, already in `frontend/package.json`) | User, "okay use material UI" |
| Look | **C. Dark graphite and teal** (dark mode only) | User clicked C on `campaign-look-again.html` |
| Campaign layout | The **first mockup** layout (simple numbered schedule strip, 4 numbers, lead table with one column per email). The later "flow" layouts (cards, vertical journey, calendar) were rejected. | User, "the current flow screens are not good" |
| Old plain HTML/JS app | **Replace `/app`** with the new React + MUI build; retire `src/outreach/web/` once every page is covered (keep it until then, and never delete another session's uncommitted edits). | User, after the builder check |
| Connectors v1 | **Status first**: cards show live status read from the current config and health data; "Connect" opens a side panel with setup steps; the agent picks changes up on restart. Real in-app sign-in (Google/Microsoft OAuth, secrets stored in DB, hot reload) is a later step. | User, "okay" to the recommendation |

Mockups (kept in `.superpowers/brainstorm/397011-1795178815/content/`, gitignored):
`campaign-look.html` (option C), `connectors-settings.html`.

## Approaches considered

1. **Rebuild the UI in React + MUI in `frontend/` (Vite, TypeScript), build into
   `src/outreach/webapp/`, serve that at `/app`, retire `src/outreach/web/`.** Chosen. MUI is a
   React library, so the user's choice requires React. `frontend/` already has a shell, theme,
   API client and Campaign/Templates components against the same `/demo/api/p0/*` routes.
2. Restyle the plain HTML/JS app with a CSS kit (daisyUI / hand CSS). Rejected: the user chose
   Material UI; plain JS has no MUI.
3. Embed MUI via CDN without a build. Rejected: no tree-shaking, no TypeScript, fragile.

## Goals

- One theme: dark graphite + teal, defined once in `frontend/src/theme.ts` (tokens below).
- App shell: left sidebar with icons. Main: Campaign, Leads, Conversations, Templates.
  "Workspace" group: Connectors, Settings. Top-right: user name (existing `user.tsx`).
- **Campaign page** (layout of mockup C):
  - Header: title, campaign state chip (Sending / Paused / Not started), secondary "Pause all"
    (or "Start"), primary "Add leads".
  - 4 stat tiles with icons: Leads in sequence, Sent today (x of y), Replies, Stopped or
    unsubscribed.
  - Email schedule strip: numbered steps 1..N on a line, each with template name and timing
    ("Right away", "Day 9", "Tue 24 Nov, 10:00"), "+ Add email" tile, "Edit" opens a side drawer
    holding today's schedule controls (template, timing type, day/date). No dropdowns on the page.
  - Lead table: tabs (All / Sending today / Replied / Stopped with counts), search; columns Lead
    (initials avatar, name, company), Status chip, Email 1..N cells with icon + colour
    (sent green check, today amber, planned grey, cancelled struck through). Row click opens the
    existing lead drawer; cell click opens the email drawer.
- **Templates page**: same data and actions as today, restyled (list + editor + preview).
- **Leads page**: lead list and CSV/Excel import (`/demo/api/import`), restyled.
- **Conversations page**: WhatsApp conversations with takeover, from the existing demo console
  data (`/demo/api/state`, `/demo/api/lead/{id}`). If that is too large for this round, ship a
  clear empty state and keep the item in the nav (builder's call, say which).
- **Connectors page** (mockup `connectors-settings.html`): summary chips (working / needs a look /
  not connected); groups "Sending email" (Gmail, Own mailboxes, Instantly), "Calendar and booking"
  (Cal.com, Google Calendar, Outlook Calendar), "Messaging, alerts and AI" (WhatsApp Business,
  Microsoft Teams, AI model, plus Email verification). Each card: logo, name, account line,
  status badge, one-line detail, one button (Manage / Fix / Connect). Connect/Manage opens a
  side drawer with numbered setup steps and the exact `.env` keys to set (names only, never
  values), then "Restart the agent to apply".
- **Settings page**: left section list (Sending, Company, Alerts, Safety, Team); one row per
  setting (label + help left, control right). **v1 is read-only**: shows current values from
  config (working days/hours from `business_hours`, mailbox daily limits, `click_alerts_need_check`,
  `click_js_confirm`, primary channel, LLM provider) with a note "Change in .env, then restart".
  Editable settings come with real connectors.
- Every page has loading (skeletons), empty and error states (existing `ApiError` messages).
- Works at 1280px+ desktop; usable down to 768px (drawer nav collapses).

## Non-goals

- No in-app OAuth, no secret storage in the DB, no hot reload of config (later step).
- No change to agent behaviour, rules, worker, schedule semantics or the `/demo/api/p0/*` contracts
  except the new read-only endpoints below.
- No light theme toggle in v1.
- No login; `/app` stays local only (same middleware as `/demo`).

## Anti-goals (failure even if it "works")

- Dense text blocks, two-line column headers, many equal-weight outlined buttons (the old look).
- Exposing any secret value (API keys, passwords, tokens, webhook URLs with tokens) through the
  connectors/settings endpoints or the UI. Status only; key **names** only.
- Logging emails, phones, message text or tokens (CLAUDE.md rule).
- Breaking the running agent or the user's runtime files (`local.db*`, `.env.local`, `.env.demo`).

## Constraints

- Python 3.10 backend; FastAPI; tests via `.venv/bin/pytest -q` must stay green.
- Node v24 is available locally; the built app must be committed or built by `scripts/run_local`
  (builder decides; the server must not need Node at runtime if the build output is present).
- Other sessions edit this repo concurrently (one edited `src/outreach/web/` at 16:09 today):
  stage files explicitly by path, never `git add -A`; retiring `src/outreach/web/` must not
  delete another session's uncommitted work (check `git status` first).
- Icons: `@mui/icons-material` (Rounded variants), one family. Brand logos on connector cards
  are local SVG files (no runtime CDN), or a coloured icon tile when no logo is available.
- No em dashes in UI copy; plain short labels.

## Theme tokens (from mockup C)

| Token | Value |
|---|---|
| page background | `#121417` |
| surface (cards, panels) | `#1a1d21` |
| raised surface / inputs | `#202328` |
| sidebar | `#16181b` |
| divider | `#2a2e34` |
| text primary / secondary | `#e7e9ec` / `#9aa1ab` |
| accent (primary) | `#2dd4bf`, text on accent `#0b1f1d`, tint `#1d2e2c` |
| success | `#4ade80` on `#173322` |
| warning | `#fbbf24` on `#3a2f12` |
| error | `#f87171` |
| radius | 8px buttons/inputs, 12px cards, pill chips |
| font | Plus Jakarta Sans (self-hosted via `@fontsource`) |

## New backend endpoints (read-only, local only, next to the p0 console routes)

- `GET /demo/api/connectors` → list of `{id, group, name, account, status: "working" |
  "attention" | "off", detail, setup: {steps: [...], env_keys: [...]}}`. Status comes from
  `*_MODE` settings, presence (not value) of required fields (`REAL_MODE_REQUIRES`), mailbox
  health data and recent unposted `integration_error` alerts. Fake/local modes show as
  "working (local test mode)". Google Calendar / Outlook Calendar have no backend yet: always
  `off` with setup text "coming soon".
- `GET /demo/api/settings` → current non-secret values grouped by section.
- Tests: values never include secrets (assert against a settings object filled with sentinel
  secrets); status mapping per mode; local-only guard.

## Calls made after the builder check (author's, correct if wrong)

- **Sent today (x of y)**: x = emails actually sent today, y = emails due today (sent + still
  planned for today). Add an additive `sent_today` field to `/demo/api/p0/board` `totals`
  (existing fields unchanged); this is the one allowed change to the p0 contract.
- **Connector status means**: `active` (this tool is the one in use and has no integration errors
  or failing health checks in the last 24 h), `attention` (in use, with recent errors or a failing
  mailbox health check), `available` (supported but not selected, e.g. the email tools not chosen
  by `EMAIL_MODE`), `coming_soon` (Google/Outlook Calendar). No new network checks in v1; the card
  shows what the status is based on ("No errors in the last 24 h", "Last booking 18 Nov") so it
  never claims more than it knows. Cal.com is `active` once a booking webhook has been received,
  otherwise `available` with "No booking received yet".
- **Settings Company / Team** are filled from what `config.py` has (signature, per-company limits,
  Teams allowed IDs as counts, not values); sections with nothing behind them are left out.

## Left to the builder

- Component breakdown, routing (`react-router-dom` already present), data fetching pattern.
- Whether to commit build output or build in `run_local`.
- How much of Conversations ships now (see above).
- Exact copy for setup steps per connector (must match the real `.env` keys in `config.py`).
- Docs: progress board row, task note, `docs/README.md` line, guide update for `/app`.
