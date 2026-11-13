"""WhatsAppSender: the policy-level WhatsApp port (actions.WaPort).

Enforces the 24 h window locally (before any HTTP), renders texts from templates/whatsapp.yaml,
sends the booking link as a tracked CTA button, and logs every outbound ChatMessage.
Flushes only; the caller (ingest / worker) commits.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..links import make_link
from ..models import ChatMessage, Lead, Suppression
from ..sequencer import _clean, _fields
from ..states import TERMINAL_VALUES
from .client import WhatsAppClient, WhatsAppNotAllowed, WindowClosed

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WA_TEMPLATES = REPO_ROOT / "templates" / "whatsapp.yaml"
TEMPLATE_NAMES = ("ozoo_intro", "ozoo_reminder")
TEXT_KEYS = ("booking_link", "ask_away", "handoff_notice", "reschedule", "booking_slots", "not_ready_close",
             "turn_limit")
TEXT_PLACEHOLDERS = frozenset({"first_name", "company", "booking_link"})
TEMPLATE_VARIABLES = frozenset({"first_name", "company"})
CTA_KEYS = ("booking_link", "turn_limit")  # sent with the tracked booking link as a button
BUTTON_MAX = 20  # Meta cta_url display_text limit
SENT_AT_FIELD = {"ozoo_intro": "wa_template_sent_at", "ozoo_reminder": "wa_reminder_sent_at"}
# Meta rejects empty template parameters; never send a blank variable.
VARIABLE_FALLBACK = {"first_name": "there", "company": "your team"}
_URL_MARKERS = ("http", "www.")
# anti-phishing: a lead-supplied name/company that looks like a link is never inserted
_VALUE_URL_MARKERS = ("http", "www.", "://")

_cache: Dict[str, Dict[str, Any]] = {}
_TERMINAL_VALUES = TERMINAL_VALUES  # states.TERMINAL as strings


def validate_wa_templates(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("whatsapp templates must be a mapping")
    templates, texts, buttons = data.get("templates"), data.get("texts"), data.get("buttons")
    if not isinstance(templates, dict) or not isinstance(texts, dict) or not isinstance(buttons, dict):
        raise ValueError("whatsapp templates need 'templates', 'texts' and 'buttons'")
    for name in TEMPLATE_NAMES:
        t = templates.get(name)
        if not isinstance(t, dict):
            raise ValueError(f"missing template {name}")
        if not isinstance(t.get("language"), str) or not t["language"].strip():
            raise ValueError(f"template {name} needs a language")
        variables = t.get("variables")
        if not isinstance(variables, list):
            raise ValueError(f"template {name} needs a variables list")
        for v in variables:
            if v not in TEMPLATE_VARIABLES:
                raise ValueError(f"template {name} uses unknown variable {v!r}")
        qr = t.get("quick_replies", [])
        if not isinstance(qr, list) or not all(isinstance(x, str) and x.strip() for x in qr):
            raise ValueError(f"template {name}: quick_replies must be a list of payload strings")
    for key in TEXT_KEYS:
        text = texts.get(key)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"text {key} is missing or empty")
        for f in _fields(text):
            if f not in TEXT_PLACEHOLDERS:
                raise ValueError(f"text {key} uses unknown placeholder {{{f}}}")
        low = text.lower()
        if any(m in low for m in _URL_MARKERS):
            raise ValueError(f"text {key} contains a raw URL; use {{booking_link}}")
    for key in CTA_KEYS:
        label = buttons.get(key)
        if not isinstance(label, str) or not label.strip() or len(label) > BUTTON_MAX:
            raise ValueError(f"button {key} must be 1..{BUTTON_MAX} characters")
    return data


def load_wa_templates(path: Optional[str] = None) -> Dict[str, Any]:
    p = str(Path(path) if path else DEFAULT_WA_TEMPLATES)
    if p not in _cache:
        with open(p, encoding="utf-8") as fh:
            _cache[p] = validate_wa_templates(yaml.safe_load(fh))
    return _cache[p]


def window_open(lead: Lead, now: datetime) -> bool:
    return lead.wa_window_until is not None and lead.wa_window_until > now


def phone_suppressed(session: Session, phone: str) -> bool:
    return session.scalar(select(Suppression.id).where(
        Suppression.value == phone, Suppression.kind == "phone").limit(1)) is not None


def check_free_text(session: Session, lead: Lead, now: datetime) -> None:
    """Raise exactly what send_raw_text would refuse with (WhatsAppNotAllowed, then WindowClosed),
    without sending: lets a caller answer a person at once and send later."""
    if not lead.consent_whatsapp or not lead.phone or phone_suppressed(session, lead.phone):
        raise WhatsAppNotAllowed(f"lead {lead.id}: no WhatsApp consent, phone or suppressed")
    if not window_open(lead, now):
        raise WindowClosed(f"lead {lead.id}: WhatsApp window closed")


class WhatsAppSender:
    """Implements actions.WaPort on top of a WhatsAppClient (or a fake with the same methods)."""

    def __init__(self, client: Any, settings: Optional[Settings] = None,
                 templates: Optional[Dict[str, Any]] = None) -> None:
        self.client = client
        self.settings = settings or get_settings()
        self.templates = validate_wa_templates(templates) if templates is not None else load_wa_templates()

    # ------------------------------------------------------------ helpers
    def _require_consent(self, session: Session, lead: Lead, author: str = "bot") -> str:
        """Checked before the window: an opt-out ("STOP") itself opens the 24 h window. Automated
        sends (templates, bot texts) never go to a lead in a terminal state; a person (Teams `wa`)
        still may, within consent and suppression."""
        if author != "human" and lead.state in _TERMINAL_VALUES:
            logger.info("lead %s: WhatsApp automated send refused, lead is %s", lead.id, lead.state)
            raise WhatsAppNotAllowed(f"lead {lead.id}: terminal state")
        phone = lead.phone
        if not lead.consent_whatsapp or not phone:
            logger.info("lead %s: WhatsApp send refused, no consent or phone", lead.id)
            raise WhatsAppNotAllowed(f"lead {lead.id}: no WhatsApp consent or phone")
        if phone_suppressed(session, phone):
            logger.info("lead %s: WhatsApp send refused, phone suppressed", lead.id)
            raise WhatsAppNotAllowed(f"lead {lead.id}: phone suppressed")
        return phone

    def _booking_url(self, lead: Lead) -> str:
        return make_link(lead.id, lead.step_sent or 0, "booking", "whatsapp", settings=self.settings)

    @staticmethod
    def _safe(field: str, value: str) -> str:
        low = value.lower()
        if any(m in low for m in _VALUE_URL_MARKERS):
            return VARIABLE_FALLBACK[field]
        return value

    def _values(self, lead: Lead) -> Dict[str, str]:
        name = _clean(lead.name)
        first = name.split(" ")[0] if name else ""
        return {"first_name": self._safe("first_name", first),
                "company": self._safe("company", _clean(lead.company))}

    def _require_window(self, lead: Lead, now: datetime) -> None:
        if not window_open(lead, now):
            logger.info("lead %s: WhatsApp free-form send refused, window closed", lead.id)
            raise WindowClosed(f"lead {lead.id}: WhatsApp window closed")

    def _log(self, session: Session, lead: Lead, author: str, text: str, wamid: str, now: datetime) -> None:
        session.add(ChatMessage(lead_id=lead.id, direction="out", author=author, text=text,
                                wa_message_id=wamid, created_at=now))
        session.flush()

    # ------------------------------------------------------------ WaPort
    def send_text(self, session: Session, lead: Lead, key: str, now: datetime, author: str = "bot") -> str:
        phone = self._require_consent(session, lead, author)
        self._require_window(lead, now)
        texts = self.templates["texts"]
        if key not in texts:
            raise ValueError(f"unknown WhatsApp text {key!r}")
        tpl = texts[key]
        url = self._booking_url(lead) if key in CTA_KEYS or "booking_link" in _fields(tpl) else ""
        text = tpl.format_map({**self._values(lead), "booking_link": url})
        if key in CTA_KEYS:
            wamid = self.client.send_cta_link(phone, text, self.templates["buttons"][key], url)
        else:
            wamid = self.client.send_text(phone, text)
        self._log(session, lead, author, text, wamid, now)
        logger.info("lead %s: WhatsApp text %s sent", lead.id, key)
        return wamid

    def send_raw_text(self, session: Session, lead: Lead, text: str, now: datetime, author: str = "human") -> str:
        phone = self._require_consent(session, lead, author)
        self._require_window(lead, now)
        wamid = self.client.send_text(phone, text)
        self._log(session, lead, author, text, wamid, now)
        logger.info("lead %s: WhatsApp %s text sent", lead.id, author)
        return wamid

    def send_template(self, session: Session, lead: Lead, name: str, now: datetime) -> str:
        phone = self._require_consent(session, lead)
        cfg = self.templates["templates"].get(name)
        if cfg is None:
            raise ValueError(f"unknown WhatsApp template {name!r}")
        vals = self._values(lead)
        variables: List[str] = [vals.get(v) or VARIABLE_FALLBACK[v] for v in cfg["variables"]]
        if cfg.get("quick_replies"):
            wamid = self.client.send_template(phone, name, cfg["language"], variables,
                                              quick_replies=cfg["quick_replies"])
        else:
            wamid = self.client.send_template(phone, name, cfg["language"], variables)
        field = SENT_AT_FIELD.get(name)
        if field:
            setattr(lead, field, now)
        self._log(session, lead, "bot", f"[template:{name}]", wamid, now)
        logger.info("lead %s: WhatsApp template %s sent", lead.id, name)
        return wamid
