# Task 02: Data model + state machine

## Status
✅ Done. Reviewed clean.

## What it does (plain language)
Defines the database tables and the single lead state. Every lead has exactly one state across both channels. Every state change must go through one function that rejects illegal moves.

## Files
- `src/outreach/db.py`, `models.py`, `states.py`
- `tests/conftest.py`, `tests/test_states.py`

## Key functions and interfaces
- `db.py`: `UTCDateTime` (rejects naive datetimes), `Base`, `SessionLocal`, `make_engine(url, **kw)`, `init_db`.
- `models.py`: `Lead`, `Event`, `ChatMessage`, `Suppression`, `Alert`.
- `states.py`: `LeadState`, `ALLOWED`, `TERMINAL`, `IllegalTransition`, `transition(session, lead, to, reason, now)`.

## Depends on
Task 1 (settings, project layout).

## Used by
Tasks 3, 4, 5, 6 now. Later: 7-12 (lead fields, transitions), 11 (booking states).

## Key decisions (from the rulings)
No task-specific rulings. `ALLOWED` includes extra paths the ledger checked against tasks 5 and 9: `IN_SEQUENCE->CLOSED`, `ENGAGED->PAUSED`, `IN_SEQUENCE->QUALIFYING`, and any non-terminal to `SUPPRESSED`.

## Tests
5: `test_allowed_path`, `test_any_non_terminal_can_be_suppressed`, `test_illegal_transition_raises`, `test_lead_needs_email_or_phone`, `test_terminal_states_are_final`.

## Commits
`deb7826` feat: lead model (both channels) and single-state machine.

## Open items / deferred minors
- `transition()` sets state before flush.
- `ALLOWED` additions are not parametrised-tested.
- No UTC round-trip test.
- `transition()` on an unflushed lead logs `lead_id` as None.
