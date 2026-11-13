# Task 01: Tool spike + project skeleton

## Status
✅ Done. Reviewed clean.

## What it does (plain language)
Sets up the Python project and settings loader. Also researches the email tool, Cal.com and WhatsApp APIs and records what each can do. Builds 17 sample webhook payloads (fixtures) that later tests use.

## Files
- `pyproject.toml`, `.env.example`, `.gitignore`
- `src/outreach/config.py`, `tests/test_config.py`
- `docs/tool-capabilities.md`
- `tests/fixtures/*.json` (17 files: `tool_*`, `cal_*`, `wa_*`)

## Key functions and interfaces
- `Settings` and cached `get_settings()` in `config.py`. Includes `intake_api_key`, JSON-dict env vars (`campaign_ids`, `link_targets`), model names, business hours.
- Tool decision: `instantly` (switchable through `TOOL_NAME`).

## Depends on
Nothing.

## Used by
Every later task (settings), tasks 6, 8, 11 (fixtures), task 6 (tool choice).

## Key decisions (from the rulings)
- Spike done from public docs, not trial accounts. Fixtures carry `_source` ("docs" or "constructed"). See [decisions.md](../decisions.md).
- Python 3.10 compatible code (`class X(str, Enum)`).

## Tests
2 at completion (`test_settings_load_from_env`, `test_secrets_required`). The file has 6 now after Task 4 added secret checks.

## Commits
`40cf02d` chore: skeleton, tool/Cal.com/WhatsApp capability spike, webhook fixtures. Review range `40cf02d..1ad7829`.

## Open items / deferred minors
- Validate `business_tz` / `business_hours` in config.
- Check `link_targets` keys against allowed link tags (checked in the Task 6 redirect).
- Pin dependency lower bounds.
- Tool choice `instantly` is provisional. Everything marked UNVERIFIED is in [go-live-checklist.md](../go-live-checklist.md).
