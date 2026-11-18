"""Email templates managed in the web app (task 22): browse, preview, write, approve, send.

- Built-in versions come from templates/emails.yaml (one per body alternative). They can be switched
  off (and on again) but not edited here; "Duplicate" in the web app starts a custom draft from one.
- Custom templates live in the `email_template` table: a sequence step version (email 1-4, one
  variant, optionally one role angle) or a one-off email the product manager sends by hand.
- Status: draft -> approved (live) -> off. Any edit puts a template back to draft. Approving,
  switching off and sending need TEMPLATE_APPROVER_PASSCODE (the product manager's).
- `templates_for(session)` is what the sequencer renders with: the YAML plus the approved custom
  versions, minus the switched-off built-in ones. A lead's emails are rendered when it is enrolled
  (and the next one again when its interest changes), so a newly approved template reaches leads
  enrolled after that.
"""
from __future__ import annotations

import hmac
import logging
import secrets
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import EmailTemplate, Event, Lead
from .outreach import _TERMINAL_VALUES, email_suppressed
from .richmail import LINK_TAGS, MAX_TEXT, TEXT_KEYS, available_images, image_file
from .sequencer import (CUSTOM_KEY, OFF_KEY, STEP_VARIANTS, _check_text, _fields, angle_for, live_versions,
                        load_templates, render_version, step_versions)

logger = logging.getLogger(__name__)

STATUSES = ("draft", "approved", "off")
KINDS = ("sequence", "manual")
STEP_LABELS = {1: "Email 1: first note (plain text)", 2: "Email 2: a real example", 3: "Email 3: start small",
               4: "Email 4: close the loop"}
VARIANT_LABELS = {"standard": "Standard", "case_study": "After reading the case study",
                  "product": "After reading a product page", "booking_slots": "After reading pricing"}
# a typical role per angle, for previews
SAMPLE_ROLES = {"collections": "Head of Collections", "quality": "VP Quality and Compliance",
                "sales": "Head of Tele-sales", "service": "Head of Customer Service", "technology": "CTO",
                "general": "Managing Director"}
REPLY_ONLY = {"answer", "follow_up_when"}
MAX_NAME = 120
MAX_SUBJECT = 150
MAX_BODY = 6000
MAX_BY = 80
MAX_SEND = 25  # leads per manual send


class TemplateError(ValueError):
    """A message for the person using the web app."""


# ---------------------------------------------------------------- reading
def _custom_version(row: EmailTemplate) -> Dict[str, Any]:
    return {"key": row.key, "kind": row.kind, "step": row.step, "variant": row.variant, "angle": row.angle,
            "subject": row.subject, "body": row.body, "rich": row.rich or None, "name": row.name}


def _rows(session: Session) -> Dict[str, EmailTemplate]:
    return {r.key: r for r in session.scalars(select(EmailTemplate))}


def templates_for(session: Session, base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The templates the sequencer renders with (a copy; the YAML cache is never changed)."""
    t = dict(base or load_templates())
    rows = list(session.scalars(select(EmailTemplate)))
    t[OFF_KEY] = [r.key for r in rows if r.source == "builtin" and r.status == "off"]
    t[CUSTOM_KEY] = [_custom_version(r) for r in rows if r.source == "custom" and r.status == "approved"]
    return t


def _iso(d: Optional[datetime]) -> Optional[str]:
    return d.isoformat() if d else None


def list_templates(session: Session, t: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Every template for the web app: built-in versions first (by step), then custom ones."""
    t = t or load_templates()
    rows = _rows(session)
    out: List[Dict[str, Any]] = []
    for step in sorted(t["steps"]):
        for variant in STEP_VARIANTS:
            for i, v in enumerate(step_versions(t, step, variant)):
                row = rows.get(v["key"])
                subjects = v["subject"] if isinstance(v["subject"], list) else [v["subject"]]
                out.append({**v, "source": "builtin", "status": row.status if row else "approved",
                            "name": f"{VARIANT_LABELS[variant]} · version {i + 1}", "subjects": subjects,
                            "updated_by": row.updated_by if row else "",
                            "updated_at": _iso(row.updated_at) if row else None,
                            "approved_by": None, "approved_at": None})
    for r in sorted((r for r in rows.values() if r.source == "custom"), key=lambda r: r.id):
        out.append({**_custom_version(r), "source": "custom", "status": r.status, "subjects": [r.subject],
                    "created_by": r.created_by, "updated_by": r.updated_by, "updated_at": _iso(r.updated_at),
                    "approved_by": r.approved_by, "approved_at": _iso(r.approved_at)})
    return out


def find_version(session: Session, key: str, t: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A template by key (built-in or custom), with its status."""
    for item in list_templates(session, t):
        if item["key"] == key:
            return item
    raise TemplateError("No such template.")


def options(t: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What the editor offers: steps, variants, angles, images, link targets."""
    t = t or load_templates()
    return {"steps": STEP_LABELS, "variants": VARIANT_LABELS, "angles": ["any", *t.get("angles", {})],
            "sample_roles": SAMPLE_ROLES, "images": available_images(), "image_links": list(LINK_TAGS),
            "max_send": MAX_SEND}


# ---------------------------------------------------------------- checking a draft
def _text(data: Dict[str, Any], key: str, limit: int) -> str:
    value = data.get(key)
    value = ("" if value is None else str(value)).replace("\r\n", "\n").strip()
    label = {"body": "email text"}.get(key, key.replace("_", " "))
    if not value:
        raise TemplateError(f"Please fill in the {label}.")
    if len(value) > limit:
        raise TemplateError(f"The {label} is too long (at most {limit} characters).")
    return value


def _clean_rich(data: Dict[str, Any], body: str, step: Optional[int]) -> Optional[Dict[str, Any]]:
    raw = data.get("rich") or {}
    if not isinstance(raw, dict):
        raise TemplateError("The design settings are not valid.")
    image = str(raw.get("image") or "").strip()
    has_image, has_button = "[[image]]" in body, "[[button]]" in body
    if step == 1 and (image or has_image or has_button):
        raise TemplateError("Email 1 stays plain text (no picture, no button): that is what gets a first "
                            "cold email into the Primary inbox. Use emails 2-4 for the designed version.")
    if image and image != "angle" and image_file(image) is None:
        raise TemplateError("Choose one of the listed pictures.")
    if has_image and not image:
        raise TemplateError("The text has [[image]]: choose a picture, or remove the [[image]] line.")
    if image and not has_image:
        raise TemplateError("Add a line [[image]] where the picture should go.")
    if not (has_image or has_button):
        return None
    rich: Dict[str, Any] = {}
    if image:
        link = str(raw.get("image_link") or "product")
        if link not in LINK_TAGS:
            raise TemplateError("Choose where a click on the picture goes.")
        rich["image"], rich["image_link"] = image, link
    for k in TEXT_KEYS:
        v = str(raw.get(k) or "").strip()
        if len(v) > MAX_TEXT:
            raise TemplateError(f"The {k.replace('_', ' ')} is too long (at most {MAX_TEXT} characters).")
        if v:
            rich[k] = v
    return rich


def _check_placeholders(where: str, text: str, need_links: bool) -> None:
    try:
        fields = set(_fields(text))
    except ValueError:
        raise TemplateError(f"{where[:1].upper()}{where[1:]} has a stray {{ or }}: use only the listed "
                            "{placeholders}.") from None
    bad = sorted(fields & REPLY_ONLY)
    if bad:
        raise TemplateError(f"{{{bad[0]}}} only works in automatic replies, not in {where}.")
    try:
        _check_text(where, text, need_links)
    except ValueError as exc:
        msg = str(exc).replace("template ", "")
        raise TemplateError(msg[:1].upper() + msg[1:] + ".") from None


def clean_draft(data: Dict[str, Any], t: Optional[Dict[str, Any]] = None, settings: Any = None) -> Dict[str, Any]:
    """The checked fields of a template the web app sent, or TemplateError with what to fix."""
    if not isinstance(data, dict):
        raise TemplateError("Nothing to save.")
    t = t or load_templates()
    kind = str(data.get("kind") or "sequence")
    if kind not in KINDS:
        raise TemplateError("Choose a sequence email or a one-off email.")
    step: Optional[int] = None
    variant, angle = "standard", str(data.get("angle") or "any")
    if kind == "sequence":
        try:
            step = int(data.get("step"))
        except (TypeError, ValueError):
            step = 0
        if step not in STEP_LABELS:
            raise TemplateError("Choose which email of the sequence this is (1 to 4).")
        variant = str(data.get("variant") or "standard")
        if variant not in STEP_VARIANTS:
            raise TemplateError("Choose when this version is used.")
    if angle != "any" and angle not in (t.get("angles") or {}):
        raise TemplateError("Choose a role group, or 'any'.")
    name = _text(data, "name", MAX_NAME)
    subject = _text(data, "subject", MAX_SUBJECT)
    if "\n" in subject:
        raise TemplateError("The subject must be one line.")
    body = _text(data, "body", MAX_BODY)
    _check_placeholders("the subject", subject, False)
    _check_placeholders("the email text", body, kind == "sequence")
    if "{unsubscribe_link}" not in body:
        raise TemplateError("The email text must contain {unsubscribe_link}: every email lets people opt out.")
    rich = _clean_rich(data, body, step)
    clean = {"name": name, "kind": kind, "step": step, "variant": variant, "angle": angle,
             "subject": subject, "body": body, "rich": rich}
    _try_render(clean, t, settings)
    return clean


def sample_lead(role: str = "", name: str = "Asha Rao", company: str = "Acme Finance",
                variant: str = "standard") -> Lead:
    """A lead that is never saved: for previews and for the render check."""
    lead = Lead(id=0, name=(name or "Asha Rao")[:200], company=(company or "Acme Finance")[:200],
                role=(role or "")[:200] or None, source="preview", state="in_sequence", step_sent=1)
    lead.variant_flags = {"variants": [variant]} if variant != "standard" else {}
    return lead


def _try_render(version: Dict[str, Any], t: Dict[str, Any], settings: Any = None) -> None:
    if settings is None:
        from .config import get_settings
        settings = get_settings()
    for role in SAMPLE_ROLES.values():
        try:
            render_version(sample_lead(role), version, version.get("step") or 2, templates=t, settings=settings,
                           preview=True)
        except (KeyError, ValueError, IndexError, AttributeError):
            raise TemplateError("The text has a stray { or }: use only the listed {placeholders}.") from None


# ---------------------------------------------------------------- writing
def _by(by: str) -> str:
    who = (by or "").strip()[:MAX_BY]
    if not who:
        raise TemplateError("Please enter your name first (top of the page): every change records who made it.")
    return who


def save_template(session: Session, data: Dict[str, Any], by: str, now: datetime, *, settings: Any,
                  key: Optional[str] = None, t: Optional[Dict[str, Any]] = None) -> EmailTemplate:
    """Create a custom template, or edit one (`key`). Either way it is a draft until approved."""
    who = _by(by)
    clean = clean_draft(data, t, settings)
    if key:
        row = session.scalar(select(EmailTemplate).where(EmailTemplate.key == key))
        if row is None or row.source != "custom":
            raise TemplateError("Only custom templates can be edited. Duplicate a built-in one to change it.")
    else:
        row = EmailTemplate(key=f"custom.{secrets.token_hex(5)}", source="custom", created_by=who, created_at=now)
        session.add(row)
    for k, v in clean.items():
        setattr(row, k, v)
    row.status, row.updated_by, row.updated_at = "draft", who, now
    row.approved_by, row.approved_at = None, None
    session.flush()
    logger.info("template %s saved as draft", row.key)
    return row


MAX_WRONG_PASSCODES = 5
LOCKOUT_S = 600
_wrong: List[float] = []  # times of recent wrong passcodes (this process)


def check_passcode(settings: Any, passcode: str, now_s: Optional[float] = None) -> None:
    """The approver passcode, constant-time. After 5 wrong tries in 10 minutes, every try is refused
    for the rest of those 10 minutes (no guessing it by brute force)."""
    secret = getattr(settings, "template_approver_passcode", None)
    expected = secret.get_secret_value() if secret else ""
    if not expected:
        raise TemplateError("Approving and sending are turned off: set TEMPLATE_APPROVER_PASSCODE in the env file.")
    now_s = time.monotonic() if now_s is None else now_s
    _wrong[:] = [t for t in _wrong if now_s - t < LOCKOUT_S]
    if len(_wrong) >= MAX_WRONG_PASSCODES:
        raise TemplateError("Too many wrong passcodes. Try again in 10 minutes.")
    if not hmac.compare_digest((passcode or "").encode(), expected.encode()):
        _wrong.append(now_s)
        raise TemplateError("That approver passcode is not right.")


def _live_standard_left(session: Session, step: int, turning_off: str, t: Dict[str, Any]) -> int:
    merged = templates_for(session, t)
    merged[OFF_KEY] = [*merged[OFF_KEY], turning_off]
    merged[CUSTOM_KEY] = [c for c in merged[CUSTOM_KEY] if c["key"] != turning_off]
    return len(live_versions(merged, step, "standard", "\0"))  # "\0": versions for every role only


def set_status(session: Session, key: str, status: str, by: str, passcode: str, settings: Any,
               now: datetime, t: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Approve (make live), switch off, or switch back on. Needs the product manager's passcode."""
    who = _by(by)
    check_passcode(settings, passcode)
    if status not in ("approved", "off"):
        raise TemplateError("Unknown action.")
    t = t or load_templates()
    item = find_version(session, key, t)
    if (status == "off" and item.get("kind") == "sequence" and item.get("variant") == "standard"
            and item.get("angle") == "any" and _live_standard_left(session, item["step"], key, t) == 0):
        raise TemplateError(f"Keep at least one live standard version of email {item['step']}.")
    row = session.scalar(select(EmailTemplate).where(EmailTemplate.key == key))
    if row is None:  # a built-in version, switched for the first time
        row = EmailTemplate(key=key, source="builtin", kind="sequence", step=item["step"], variant=item["variant"],
                            name=item["name"], created_by=who, created_at=now, updated_at=now)
        session.add(row)
    row.status, row.updated_by, row.updated_at = status, who, now
    if status == "approved":
        row.approved_by, row.approved_at = who, now
    session.flush()
    logger.info("template %s set to %s", key, status)
    return find_version(session, key, t)


# ---------------------------------------------------------------- preview and sending
def preview(session: Session, settings: Any, *, key: Optional[str] = None, draft: Optional[Dict[str, Any]] = None,
            role: str = "", name: str = "Asha Rao", company: str = "Acme Finance") -> Dict[str, Any]:
    """{"subject", "text", "html", "angle"} as a lead with `role` would get it. Links go straight to
    the real pages (no tracked link, no event)."""
    t = templates_for(session)
    version = clean_draft(draft, t, settings) if draft is not None else find_version(session, key or "", t)
    lead = sample_lead(role, name, company, version.get("variant") or "standard")
    out = render_version(lead, version, version.get("step") or 2, templates=t, settings=settings, preview=True)
    return {**out, "angle": angle_for(lead, t)}


def eligibility(session: Session, lead: Lead) -> Optional[str]:
    """Why a manual email can't go to this lead (None = it can)."""
    if not lead.email:
        return "no email address"
    if lead.state in _TERMINAL_VALUES:
        return "closed, opted out or finished"
    if email_suppressed(session, lead.email):
        return "unsubscribed or blocked"
    if not lead.tool_lead_id or (lead.step_sent or 0) < 1:
        return "no email sent yet (a manual email goes as a reply in the lead's thread)"
    return None


def send_manual(session: Session, key: str, lead_ids: Iterable[int], *, by: str, passcode: str, settings: Any,
                tool: Any, now: datetime) -> List[Dict[str, Any]]:
    """Send an approved template to the chosen leads, as a reply in each lead's email thread."""
    who = _by(by)
    check_passcode(settings, passcode)
    try:
        ids = list(dict.fromkeys(int(i) for i in lead_ids))
    except (TypeError, ValueError):
        raise TemplateError("Choose leads from the list.") from None
    if not ids:
        raise TemplateError("Choose at least one lead.")
    if len(ids) > MAX_SEND:
        raise TemplateError(f"At most {MAX_SEND} leads at a time.")
    t = templates_for(session)
    version = find_version(session, key, t)
    if version["status"] != "approved":
        raise TemplateError("Only approved templates can be sent.")
    return [_send_one(session, lead_id, key, version, who, settings, tool, now, t) for lead_id in ids]


def _send_one(session: Session, lead_id: int, key: str, version: Dict[str, Any], who: str, settings: Any,
              tool: Any, now: datetime, t: Dict[str, Any]) -> Dict[str, Any]:
    lead = session.get(Lead, lead_id)
    reason = "no such lead" if lead is None else eligibility(session, lead)
    if reason:
        return {"lead_id": lead_id, "ok": False, "reason": reason}
    out = render_version(lead, version, max(lead.step_sent, 1), templates=t, settings=settings)
    try:
        ok = bool(tool.reply(lead.tool_lead_id, out["text"], html=out["html"] or None))
    except Exception as exc:  # noqa: BLE001 - one lead's failure must not stop the others
        logger.warning("lead %s: manual email failed: %s", lead_id, type(exc).__name__)
        ok = False
    if not ok:
        return {"lead_id": lead_id, "ok": False, "reason": "the email tool could not send it"}
    session.add(Event(lead_id=lead.id, channel="email", kind="manual_email", occurred_at=now, received_at=now,
                      step=lead.step_sent, processed=True,
                      data={"template": key, "name": version.get("name", ""), "by": who}))
    session.flush()
    logger.info("lead %s: manual email %s sent", lead_id, key)
    return {"lead_id": lead_id, "ok": True, "reason": None}
