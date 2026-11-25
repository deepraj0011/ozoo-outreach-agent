# Task 31: web app redesign

**Date:** 2026-11-20 · **Status:** done · **Tests:** 2028 passing (`.venv/bin/pytest -q`)

## Asked

Replace the plain HTML app with a React + Material UI web app (spec: `docs/superpowers/specs/2026-11-20-web-app-redesign-design.md`, plan: `docs/superpowers/plans/2026-11-20-web-app-redesign.md`).

## Built

- `frontend/`: Vite + React + MUI, dark teal theme, app shell and API client. Pages: Campaign board, Leads, Conversations, Templates, Connectors, Settings.
- Backend routes for the app: `/demo/api/connectors`, `/demo/api/settings` (read-only) and `sent_today` on the board.
- This task: `web_app.register(app, web_dir)` serves the build at `/app/` (`Cache-Control: no-cache`, any non-file path falls back to `index.html`, `/app` redirects to `/app/`, 503 page "not built yet" when there is no build). Still local only (demo console middleware). `create_app(..., web_dir=None)` passes the folder through.
- `scripts/build_web` (`npm ci && npm run build` into `src/outreach/webapp/`); `scripts/run_local` builds when the output is missing or older than `frontend/src`, and warns when `npm` is missing.
- Retired `src/outreach/web/` (the plain HTML/JS app). No remaining references outside historic notes.
- Tests: `tests/test_web_app.py` (built index, deep-link fallback, missing build 503, asset served, not served outside local, refused through a tunnel or a foreign host).

## Decisions

See [decisions.md](../decisions.md#task-31-web-app-redesign-2026-11-20): plain app replaced; build output not committed; connectors status-first (no OAuth yet); settings read-only in v1.

## Smoke test

Isolated temp dir (copy of the demo env with its own DB, fake email, no worker, port 8010): `/app/`, `/campaign`, `/leads`, `/templates`, `/conversations`, `/connectors`, `/settings` all return the app; reloading `/app/connectors` works; headless Chrome screenshots render the pages.
