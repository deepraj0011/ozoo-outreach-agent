"""Read-only settings for the Settings screen, in plain language.

Only non-secret fields are read. Teams allowed ids appear as a count, never as values."""
from __future__ import annotations

from typing import Any, Dict, List

from outreach.hours import DAYS, parse_business_hours

Row = Dict[str, Any]


def _row(label: str, help_text: str, value: Any) -> Row:
    return {"label": label, "help": help_text, "value": value}


def _sending(settings: Any) -> List[Row]:
    hours = parse_business_hours(settings.business_hours)
    rows = [
        _row("Working days", "Days the team is at work.", [DAYS[i].capitalize() for i in sorted(hours.days)]),
        _row("Working hours", "Opening and closing time.", f"{hours.open:%H:%M} to {hours.close:%H:%M}"),
    ]
    mailboxes = list(getattr(settings, "mailboxes", None) or [])
    if mailboxes:
        rows.append(
            _row("Daily limit per mailbox", "The most each mailbox sends in a day.",
                 ", ".join(str(m.daily_limit) for m in mailboxes))
        )
    rows.append(
        _row("Main channel when both are allowed", "Where we write first when a lead can get email and WhatsApp.",
             settings.primary_when_both)
    )
    return rows


def _safety(settings: Any) -> List[Row]:
    return [
        _row("Only alert sales after a real visit", "A click alone is not enough to alert sales.",
             bool(settings.click_alerts_need_check)),
        _row("Confirm clicks with a script check", "A page script confirms a person read the page.",
             bool(settings.click_js_confirm)),
    ]


def _alerts(settings: Any) -> List[Row]:
    target = "Microsoft Teams" if settings.alerts_mode == "real" else "Console (local test)"
    return [_row("Alerts go to", "Where sales alerts are posted.", target)]


def _company(settings: Any) -> List[Row]:
    return [_row("Sender name", "The name shown on emails we send.", settings.gmail_from_name)]


def _team(settings: Any) -> List[Row]:
    count = len(settings.teams_allowed_aad_id_set)
    noun = "person" if count == 1 else "people"
    return [_row("Sales people who can reply from Teams", "Only the number is shown.", f"{count} {noun}")]


def settings_view(settings: Any) -> Dict[str, Any]:
    sections = [
        ("sending", "Sending", _sending(settings)),
        ("safety", "Safety", _safety(settings)),
        ("alerts", "Alerts", _alerts(settings)),
        ("company", "Company", _company(settings)),
        ("team", "Team", _team(settings)),
    ]
    return {"sections": [{"id": i, "title": t, "rows": r} for i, t, r in sections if r]}
