# Task 05: Rules engine (both channels)

## Status
✅ Done. Reviewed clean after fix round 1.

## What it does (plain language)
The brain. Given a lead snapshot and an event, it returns an ordered list of actions (send, alert, change state, suppress, and so on). It is pure: no DB, no network, no clock. It enforces "one channel at a time", opt-outs in any state, and ignores events for leads in terminal states.

## Files
- `src/outreach/actions.py` (dataclasses at this stage), `rules.py`
- `tests/test_rules.py`; `score.py` gained `points_for`

## Key functions and interfaces
- `decide(lead: LeadSnapshot, event: EventSnapshot, now) -> list[Action]`.
- `LeadSnapshot.from_lead(lead, now)`, `EventSnapshot.from_event(event)`.
- Actions: `SetState`, `Suppress`, `SuppressAllChannels`, `ToolStop/Pause/Resume`, `WaCancelReminder`, `WaSendText/Template`, `SwitchChannel`, `SetVariant`, `AddScore`, `SetFlag`, `SetStepSent`, `RefreshNextStep`, `Alert`, `Classify`, `BotReply`, `SendReply`, `PauseColleagues`, `HandOff`, `StopAllOutreach`. `flatten()` expands composites.

## Depends on
Task 2 (states), Task 3 (channel logic), Task 4 (score, bands).

## Used by
Task 6 (executor and ingest call `decide`). Task 7 (`SwitchChannel`, variants), 9, 10, 11, 12 (all produce or consume these actions).

## Key decisions (from the rulings)
- Unsubscribe link click does nothing; only an `unsubscribe` event (POST) opts out. Scanners pre-click links.
- Hard bounce only switches or suppresses from outreach states on the email channel. Other states get Suppress(email) plus ToolStop.
- Booking from a terminal lead raises a "booked" alert.
- Counters are emitted as `SetFlag`, not mutated in the executor.

## Tests
256 test cases in `test_rules.py` (85 functions, parametrised). Includes an invariant test that every `SetState` chain is legal.

## Commits
`ddcee6f` feat: pure rules engine for email + WhatsApp events. `144aa86` fix: scanner-safe unsubscribe, scoped hard-bounce effects, terminal booking alert.

## Open items / deferred minors
- `Alert.text` is excluded from equality.
- A hard bounce with `active_channel=None` in an outreach state is still suppressed (guard uses `== "whatsapp"`, not `!= "email"`). Unlikely.
- `no_show` acts only in BOOKED. Other states return nothing.
- Hard bounce while WhatsApp is active suppresses fully (may be too strict).
