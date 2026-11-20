"""P0 template library: the emails the product manager writes and puts on the schedule."""
from __future__ import annotations

import logging
import string
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Event, Lead, P0ScheduleRow, P0Template, PlannedEmail
from ..sequencer import (
    ALLOWED_PLACEHOLDERS, load_templates, render_version, step_versions,
)
from ..template_library import MAX_BODY, MAX_BY, MAX_NAME, MAX_SUBJECT, STEP_LABELS, sample_lead

logger = logging.getLogger(__name__)

STATUSES = ("draft", "live", "off")
BUTTON = "Book a 30-minute demo"
STARTER_DAYS = {1: 0, 2: 4, 3: 9, 4: 15}
UNSUBSCRIBE_LINE = "\n\nUnsubscribe: {unsubscribe_link}"
# {answer} / {follow_up_when} only exist in automatic replies.
PLACEHOLDERS: Tuple[str, ...] = tuple(sorted(
    (ALLOWED_PLACEHOLDERS - {"answer", "follow_up_when"}) | {"name", "role"}))


class P0Error(ValueError):
    """A message the web app can show as is."""


def _text(data: Dict[str, Any], key: str, limit: int, label: str) -> str:
    value = ("" if data.get(key) is None else str(data[key])).replace("\r\n", "\n").strip()
    if not value:
        raise P0Error(f"Please fill in the {label}.")
    if len(value) > limit:
        raise P0Error(f"The {label} is too long (at most {limit} characters).")
    return value


def _check_placeholders(where: str, text: str) -> None:
    try:
        fields = {f for _, f, _, _ in string.Formatter().parse(text) if f}
    except ValueError:
        raise P0Error(f"The {where} has a stray {{ or }}: use only the listed placeholders.") from None
    for f in sorted(fields):
        if f not in PLACEHOLDERS:
            raise P0Error(f"The {where} uses an unknown placeholder {{{f}}}.")


def _who(by: str) -> str:
    who = (by or "").strip()[:MAX_BY]
    if not who:
        raise P0Error("Please enter your name first: every change records who made it.")
    return who


def _log(session: Session, template_id: int, action: str, by: str, now: datetime) -> None:
    session.add(Event(channel="system", kind="p0_template", occurred_at=now, received_at=now,
                      data={"id": template_id, "action": action, "by": by}))


def _used_by(session: Session) -> Dict[int, List[int]]:
    used: Dict[int, List[int]] = {}
    for row in session.scalars(select(P0ScheduleRow).order_by(P0ScheduleRow.email_no)):
        used.setdefault(row.template_id, []).append(row.email_no)
    return used


def list_templates(session: Session) -> List[Dict[str, Any]]:
    used = _used_by(session)
    rows = session.scalars(select(P0Template).order_by(P0Template.id)).all()
    return [{"id": t.id, "name": t.name, "subject": t.subject, "body": t.body, "status": t.status,
             "used_by": used.get(t.id, []), "updated_at": t.updated_at.isoformat(), "updated_by": t.updated_by}
            for t in rows]


def _clean(data: Dict[str, Any]) -> Dict[str, str]:
    clean = {"name": _text(data, "name", MAX_NAME, "name"),
             "subject": _text(data, "subject", MAX_SUBJECT, "subject"),
             "body": _text(data, "body", MAX_BODY, "email text")}
    if "\n" in clean["subject"]:
        raise P0Error("The subject must be one line.")
    _check_placeholders("subject", clean["subject"])
    _check_placeholders("email text", clean["body"])
    return clean


def _version(tpl: Any, with_unsubscribe: bool = True) -> Dict[str, Any]:
    body = tpl.body
    if with_unsubscribe and "{unsubscribe_link}" not in body:
        body += UNSUBSCRIBE_LINE  # every email carries the opt-out line, whatever the PM wrote
    return {"key": f"p0.{tpl.id}", "subject": tpl.subject, "body": body, "rich": {"button": BUTTON}}


def render(lead: Lead, tpl: P0Template, email_no: int, *, settings: Any) -> Dict[str, str]:
    return render_version(lead, _version(tpl), email_no, templates=load_templates(), settings=settings)


def save_template(session: Session, data: Dict[str, Any], by: str, now: datetime, *,
                  settings: Any) -> P0Template:
    """Create (no `id`: a draft unless `status` is given) or edit (the status stays)."""
    who = _who(by)
    clean = _clean(data)
    tpl_id = data.get("id")
    if tpl_id is None:
        status = data.get("status") or "draft"
        if status not in STATUSES:
            raise P0Error("The status must be draft, live or off.")
        tpl = P0Template(status=status, created_by=who, created_at=now, **clean)
        session.add(tpl)
        action = "created"
    else:
        tpl = session.get(P0Template, tpl_id)
        if tpl is None:
            raise P0Error("That template does not exist.")
        for k, v in clean.items():
            setattr(tpl, k, v)
        action = "edited"
    tpl.updated_by, tpl.updated_at = who, now
    _check_renders(tpl, settings)
    session.flush()
    _log(session, tpl.id, action, who, now)
    logger.info("p0 template %s %s", tpl.id, action)
    return tpl


def _check_renders(tpl: Any, settings: Any) -> None:
    try:
        render_version(sample_lead(), _version(tpl), 1, templates=load_templates(), settings=settings,
                       preview=True)
    except (KeyError, ValueError, IndexError, AttributeError):
        raise P0Error("The text has a stray { or }: use only the listed placeholders.") from None


def set_status(session: Session, template_id: int, status: str, by: str, now: datetime) -> P0Template:
    who = _who(by)
    if status not in STATUSES:
        raise P0Error("The status must be draft, live or off.")
    tpl = session.get(P0Template, template_id)
    if tpl is None:
        raise P0Error("That template does not exist.")
    if status != "live":
        on = _used_by(session).get(tpl.id)
        if on:
            raise P0Error(f"This template is on the schedule as email {on[0]}: choose another "
                          "template there first.")
        unsent = session.scalar(select(PlannedEmail.id).where(
            PlannedEmail.template_id == tpl.id, PlannedEmail.status.in_(("planned", "handed"))).limit(1))
        if unsent is not None:
            raise P0Error("Some leads still have this template planned or waiting to go out, "
                          "so it cannot be switched off yet.")
    tpl.status, tpl.updated_by, tpl.updated_at = status, who, now
    session.flush()
    _log(session, tpl.id, f"status:{status}", who, now)
    return tpl


def preview(session: Session, settings: Any, *, template_id: Optional[int] = None,
            draft: Optional[Dict[str, Any]] = None, role: str = "Head of Collections") -> Dict[str, Any]:
    """{"subject", "text", "html"} as a lead with `role` would get it; links are the real pages."""
    if draft is not None:
        source: Any = type("Draft", (), {**_clean(draft), "id": 0})()
    else:
        source = session.get(P0Template, template_id) if template_id is not None else None
        if source is None:
            raise P0Error("That template does not exist.")
    return render_version(sample_lead(role), _version(source), 1, templates=load_templates(),
                          settings=settings, preview=True)


def ensure_starter(session: Session, now: datetime, *, settings: Any) -> None:
    """First use: the four standard built-in emails become live templates on days 0 / 4 / 9 / 15."""
    if session.scalar(select(P0Template.id).limit(1)) is not None:
        return
    built_in = load_templates()
    for step in sorted(STARTER_DAYS):
        v = step_versions(built_in, step, "standard")[0]
        subject = v["subject"][0] if isinstance(v["subject"], list) else v["subject"]
        tpl = P0Template(name=STEP_LABELS[step], subject=subject, body=v["body"], status="live",
                         created_by="starter", updated_by="starter", created_at=now, updated_at=now)
        session.add(tpl)
        session.flush()
        session.add(P0ScheduleRow(email_no=step, template_id=tpl.id, day=STARTER_DAYS[step]))
    session.flush()
