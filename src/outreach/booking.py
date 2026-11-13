"""Cal.com (plan sections 8, 9): webhook parsing (X-Cal-Signature-256), qualifying-form fit, and the
sales handoff summary sent to Microsoft Teams when a meeting ends.

Leads are matched (ingest.match_lead) by the signed metadata.lead token first (our booking redirect
appends it, so a forwarded or WhatsApp booking still lands on the right lead), then by attendee
email, then by attendee phone. Reminders (24 h / 1 h) are Cal.com workflows, not code here.

Payload shapes come from Cal.com's public webhook docs (fixtures marked "_source"); fields marked
UNVERIFIED must be checked against the first real webhook (go-live checklist).
Never logged: emails, phones, names, form answers, tokens. Logged: trigger names, lead / event ids.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .botfilter import Scanners
from .channels import normalize_phone, whatsapp_usable
from .config import Settings
from .emailtool.base import BadSignature, NormalizedEvent, header
from .ingest import ingest
from .links import parse_lead_token
from .models import ChatMessage, Event, Lead
from .score import band

if TYPE_CHECKING:  # pragma: no cover
    from .actions import Alerter, Deps

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Cal-Signature-256"
SIGNATURE_PREFIX = "sha256="
TRIGGERS = {
    "BOOKING_CREATED": "booking_created",
    "BOOKING_RESCHEDULED": "booking_rescheduled",
    "BOOKING_CANCELLED": "booking_cancelled",
    "BOOKING_NO_SHOW_UPDATED": "no_show",
    "MEETING_ENDED": "meeting_ended",
    "FORM_SUBMITTED": "form_submitted",
}
BOOKING_KINDS = frozenset(k for k in TRIGGERS.values() if k != "form_submitted")

PROVIDER_ID_MAX = 200  # Event.provider_event_id column
ID_PART_MAX = 100
TIME_MAX = 40
EVENT_TYPE_MAX = 100
EMAIL_MAX = 320
PHONE_RAW_MAX = 40
RESPONSE_MAX = 500  # attacker-supplied form answers are capped before storage
RESPONSE_FIELDS_MAX = 30
FIELD_KEY_MAX = 64
LIST_ITEMS_MAX = 20
TRIGGER_LOG_MAX = 50

SIZE_FIELD = "company_size"
TIMELINE_FIELD = "timeline"
EMAIL_FIELD = "email"
PHONE_FIELDS = ("attendeePhoneNumber", "phone")

# fit defaults (the same as Settings' defaults) for callers without settings
_DEFAULT_FIT = {"fit_high_min_size": 50, "fit_high_max_months": 3,
                "fit_medium_min_size": 10, "fit_medium_max_months": 6}
NO_TIMELINE = 99  # "exploring", "not sure", unknown
FIT_TEXT_MAX = 100
_NUMBER = re.compile(r"\d{1,9}")
_NOW_WORDS = re.compile(r"\b(now|immediately|asap|right away|urgent(ly)?)\b")
_VAGUE_WORDS = re.compile(r"\b(exploring|not sure|unsure|just looking|no timeline|don'?t know)\b")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

SUMMARY_MAX = 3500
REPLIES_MAX = 3
REPLY_TEXT_MAX = 300
REPLY_SCAN = 50
CHAT_MAX = 20
CHAT_TEXT_MAX = 200
ANSWER_MAX = 200


# ====================================================================== signature + parsing

def verify_signature(headers: Mapping[str, str], raw: bytes, secret: str) -> None:
    """X-Cal-Signature-256 = hex HMAC-SHA256(secret, raw body), bare or with a "sha256=" prefix,
    hex in either case. Constant-time; before any parsing."""
    got = (header(headers, SIGNATURE_HEADER) or "").strip().lower()
    if got.startswith(SIGNATURE_PREFIX):
        got = got[len(SIGNATURE_PREFIX):]
    if not secret or not got:
        raise BadSignature(SIGNATURE_HEADER)
    want = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(got.encode(), want.encode()):
        raise BadSignature(SIGNATURE_HEADER)


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw or len(raw) > TIME_MAX:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _id(raw: Any) -> Optional[str]:
    """A provider id: non-empty string or integer (not bool), capped."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:ID_PART_MAX]
    return None


def _short_str(raw: Any, cap: int) -> Optional[str]:
    return raw[:cap] if isinstance(raw, str) and raw else None


def _provider_id(*parts: str) -> str:
    full = ":".join(parts)
    if len(full) <= PROVIDER_ID_MAX:
        return full
    return f"{parts[0]}:{parts[1]}:h{hashlib.sha256(full.encode()).hexdigest()[:40]}"


def _norm_key(name: Any) -> str:
    return _NON_ALNUM.sub("_", str(name).lower()).strip("_") if isinstance(name, str) else ""


def _answer(item: Any) -> Optional[str]:
    """One form / booking response as text. Cal.com sends {label, value} and (routing forms) a
    human-readable `response` (UNVERIFIED which one select fields fill): prefer `response`."""
    if isinstance(item, dict):
        for key in ("response", "value"):
            value = item.get(key)
            got = None if isinstance(value, dict) else _answer(value)  # no nesting
            if got:
                return got
        return None
    if isinstance(item, bool):
        return "yes" if item else "no"
    if isinstance(item, (int, float)):
        return str(item)
    if isinstance(item, str):
        return item.strip()[:RESPONSE_MAX] or None
    if isinstance(item, list):
        parts = [p for p in (_answer(x) for x in item[:LIST_ITEMS_MAX] if not isinstance(x, (dict, list))) if p]
        return ", ".join(parts)[:RESPONSE_MAX] or None
    return None


def _field(responses: Any, name: str) -> Optional[str]:
    """Response for field `name`: by key, else by an item's label / identifier (normalised)."""
    if not isinstance(responses, dict):
        return None
    if name in responses:
        return _answer(responses[name])
    want = _norm_key(name)
    for key, item in list(responses.items())[:RESPONSE_FIELDS_MAX * 4]:
        cands = [key] + ([item.get("label"), item.get("identifier")] if isinstance(item, dict) else [])
        if any(_norm_key(c) == want for c in cands):
            return _answer(item)
    return None


def _clean_responses(responses: Any) -> Dict[str, str]:
    if not isinstance(responses, dict):
        return {}
    out: Dict[str, str] = {}
    for key, item in responses.items():
        if len(out) >= RESPONSE_FIELDS_MAX:
            break
        value = _answer(item)
        if isinstance(key, str) and key and value:
            out[key[:FIELD_KEY_MAX]] = value
    return out


def _email(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    e = raw.strip().lower()
    return e if 3 <= len(e) <= EMAIL_MAX and "@" in e else None


def _attendees(body: Mapping[str, Any]) -> List[Dict[str, Any]]:
    raw = body.get("attendees")
    return [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else []


def _phone(body: Mapping[str, Any], attendees: List[Dict[str, Any]]) -> Optional[str]:
    candidates = [_field(body.get("responses"), f) for f in PHONE_FIELDS]
    candidates += [a.get("phoneNumber") for a in attendees]  # UNVERIFIED: attendee phone field
    for raw in candidates:
        if isinstance(raw, str) and raw.strip() and len(raw) <= PHONE_RAW_MAX:
            phone = normalize_phone(raw.strip(), None)
            if phone:
                return phone
    return None


def parse_cal_webhook(headers: Mapping[str, str], raw: bytes, secret: str, *,
                      received_at: Optional[datetime] = None,
                      settings: Optional[Settings] = None) -> List[NormalizedEvent]:
    """Verify, then map the single event in a Cal.com webhook (channel "cal").

    BadSignature on auth failure; ValueError (-> 400) for an undecodable / malformed body.
    Unknown triggers and no-show updates that mark nobody as absent give [].
    Both the wrapped shape ({"triggerEvent", "payload": {...}}) and the flat one
    (MEETING_ENDED: booking fields at the top level) are accepted.
    """
    verify_signature(headers, raw, secret)
    try:
        top = json.loads(raw)
    except RecursionError as exc:  # absurdly nested JSON
        raise ValueError("malformed webhook body") from exc
    if not isinstance(top, dict):
        raise ValueError("payload is not an object")
    trigger = top.get("triggerEvent")
    if not isinstance(trigger, str) or not trigger:
        raise ValueError("no triggerEvent")
    kind = TRIGGERS.get(trigger)
    if kind is None:
        safe = _NON_ALNUM.sub("_", trigger.lower())[:TRIGGER_LOG_MAX]
        logger.info("cal webhook: ignoring trigger %s", safe)
        return []
    if "payload" in top:
        body = top["payload"]
        if not isinstance(body, dict):
            raise ValueError("payload is not an object")
    else:
        body = top  # flat shape (MEETING_ENDED)

    attendees = _attendees(body)
    if kind == "no_show":
        absent = [a for a in attendees if a.get("noShow") is True]
        if not absent:
            logger.info("cal webhook: no-show update without an absent attendee skipped")
            return []
        attendees = absent

    metadata = body.get("metadata")
    lead_id = parse_lead_token(metadata.get("lead"), settings=settings) if isinstance(metadata, dict) else None
    email = next((e for e in (_email(a.get("email")) for a in attendees) if e), None)
    if email is None:
        email = _email(_field(body.get("responses"), EMAIL_FIELD))
    phone = _phone(body, attendees)
    responses = _clean_responses(body.get("responses"))

    data: Dict[str, Any] = {}
    if kind == "form_submitted":
        form_id = _id(body.get("formId")) or "form"
        response_id = _id(body.get("responseId"))
        if response_id is None:  # UNVERIFIED: responseId; else a stable hash of the answers
            digest = json.dumps(body.get("responses"), sort_keys=True, default=str)
            response_id = "r" + hashlib.sha256(digest.encode()).hexdigest()[:32]
        provider_id = _provider_id("cal", trigger, form_id, response_id)
        raw_responses = body.get("responses")
        data["fit"] = fit_from_answers(_field(raw_responses, SIZE_FIELD), _field(raw_responses, TIMELINE_FIELD),
                                       settings=settings)
        data["form_id"] = form_id
    else:
        uid = _id(body.get("uid")) or _id(body.get("bookingUid"))
        if uid is None:
            raise ValueError("booking event without uid")
        parts = ["cal", trigger, uid] + (["noshow"] if kind == "no_show" else [])
        provider_id = _provider_id(*parts)
        data["uid"] = uid
        for key, field in (("start", "startTime"), ("end", "endTime")):
            value = _short_str(body.get(field), TIME_MAX)
            if value:
                data[key] = value
        event_type = _short_str(body.get("type"), EVENT_TYPE_MAX)  # UNVERIFIED: event type slug
        if event_type:
            data["event_type"] = event_type
    if responses:
        data["responses"] = responses

    occurred = (_parse_ts(top.get("createdAt")) or
                (_parse_ts(body.get("endTime")) if kind == "meeting_ended" else None) or received_at)
    if occurred is None:
        raise ValueError("webhook event without a time")
    logger.info("cal webhook: %s", kind)
    return [NormalizedEvent(
        provider_event_id=provider_id, channel="cal", kind=kind, occurred_at=occurred,
        email=email, phone=phone, data=data, lead_id=lead_id,
    )]


def handle_cal_event(session: Session, events: Iterable[NormalizedEvent], deps: "Deps", now: datetime,
                     scanners: Optional[Scanners] = None) -> None:
    """Cal.com events go through the shared ingest (match -> store once -> decide -> execute)."""
    ingest(session, events, deps, now, scanners or Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=()))


# ====================================================================== fit

def _size_lower(text: Optional[str]) -> Optional[int]:
    """Lower bound of a company-size answer: "51-200" -> 51, "200+" -> 200, "1,000+" -> 1000."""
    if not isinstance(text, str):
        return None
    m = _NUMBER.search(text[:FIT_TEXT_MAX].replace(",", ""))
    return int(m.group(0)) if m else None


def _months(text: Optional[str]) -> int:
    """Timeline answer in months (lower bound); NO_TIMELINE when vague or unknown."""
    if not isinstance(text, str) or not text.strip():
        return NO_TIMELINE
    t = text[:FIT_TEXT_MAX].lower()
    if _NOW_WORDS.search(t):
        return 0
    if _VAGUE_WORDS.search(t):
        return NO_TIMELINE
    m = _NUMBER.search(t)
    if m is None:
        return NO_TIMELINE
    n = int(m.group(0))
    if "year" in t:
        return n * 12
    if "week" in t or "day" in t:
        return 0
    return n


def fit_from_answers(size: Optional[str], timeline: Optional[str], *,
                     settings: Optional[Settings] = None) -> str:
    """high = size >= high_min AND months <= high_max; medium = size >= medium_min OR
    months <= medium_max; else low. Thresholds from Settings (sales lead confirms them)."""
    t = {k: getattr(settings, k) for k in _DEFAULT_FIT} if settings is not None else _DEFAULT_FIT
    n, months = _size_lower(size), _months(timeline)
    if n is not None and n >= t["fit_high_min_size"] and months <= t["fit_high_max_months"]:
        return "high"
    if (n is not None and n >= t["fit_medium_min_size"]) or months <= t["fit_medium_max_months"]:
        return "medium"
    return "low"


# ====================================================================== handoff summary

def _flat(text: Any, cap: int) -> str:
    """Third-party text on one line (newlines would split it across card blocks), capped."""
    return " ".join(str(text or "").split())[:cap]


def _latest(session: Session, lead: Lead, kinds: Iterable[str]) -> Optional[Event]:
    return session.scalar(select(Event).where(Event.lead_id == lead.id, Event.kind.in_(list(kinds)))
                          .order_by(Event.occurred_at.desc(), Event.id.desc()).limit(1))


def build_handoff_summary(session: Session, lead: Lead) -> str:
    """Plain text for the sales handoff alert (the alerter escapes it for Teams). At most SUMMARY_MAX
    characters; the oldest chat lines are dropped first when it is too long."""
    session.flush()
    channels = [c for c, ok in (("email", bool(lead.email)), ("whatsapp", whatsapp_usable(lead))) if ok]
    head = [
        f"Handoff: lead {lead.id}: {_flat(lead.name, 200)}, {_flat(lead.role, 200) or '-'} at "
        f"{_flat(lead.company, 200)} (source: {_flat(lead.source, 100)})",
        f"Channels: {', '.join(channels) or 'none'}; active: {lead.active_channel or 'none'}"
        f"{'; switched channel once' if lead.channel_switched else ''}",
        f"Score: {lead.score or 0} ({band(lead.score or 0)})",
    ]

    bk = _latest(session, lead, ("booking_created", "booking_rescheduled"))
    if bk is not None:
        d = bk.data or {}
        head.append(f"Booking: {_flat(d.get('start'), TIME_MAX) or '?'} to {_flat(d.get('end'), TIME_MAX) or '?'}")
    else:
        head.append("Booking: none")

    form = _latest(session, lead, ("form_submitted",))
    if form is not None:
        d = form.data or {}
        answers = d.get("responses") if isinstance(d.get("responses"), dict) else {}
        head.append(f"Qualifying form: fit {_flat(d.get('fit'), 10) or '?'}")
        head.append("Form answers: " + ("; ".join(f"{_flat(k, FIELD_KEY_MAX)}: {_flat(v, ANSWER_MAX)}"
                                                 for k, v in answers.items()) or "none"))
    else:
        head.append("Qualifying form: none")

    steps = sorted({s for s in session.scalars(
        select(Event.step).where(Event.lead_id == lead.id, Event.kind == "sent", Event.channel == "email",
                                 Event.step.is_not(None)))})
    head.append("Emails sent: " + (f"steps {', '.join(str(s) for s in steps)}" if steps else "none"))

    recent = session.scalars(select(Event).where(Event.lead_id == lead.id, Event.kind.in_(("reply", "wa_text")))
                             .order_by(Event.occurred_at.desc(), Event.id.desc()).limit(REPLY_SCAN))
    replies = []
    for ev in recent:
        cls = (ev.data or {}).get("classification")
        if isinstance(cls, dict) and cls.get("label"):
            replies.append(f"- {_flat(cls['label'], 30)} ({ev.channel}): {_flat(ev.data.get('text'), REPLY_TEXT_MAX)}")
            if len(replies) >= REPLIES_MAX:
                break
    head.append("Classified replies (newest first):" if replies else "Classified replies: none")
    head += replies

    rows = list(session.scalars(select(ChatMessage).where(ChatMessage.lead_id == lead.id)
                                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(CHAT_MAX)))
    rows.reverse()
    chat = [f"{m.author}: {_flat(m.text, CHAT_TEXT_MAX)}" for m in rows]

    def render(lines: List[str], omitted: bool) -> str:
        if not lines:
            return "\n".join(head + ["WhatsApp transcript: none"])
        title = "WhatsApp transcript (oldest first" + (", earlier lines omitted):" if omitted else "):")
        return "\n".join(head + [title] + lines)

    omitted = False
    text = render(chat, omitted)
    while len(text) > SUMMARY_MAX and chat:
        chat.pop(0)
        omitted = True
        text = render(chat, omitted)
    if len(text) > SUMMARY_MAX:
        text = text[:SUMMARY_MAX - 1] + "…"
    return text


def make_handoff(alerter: "Alerter") -> Callable[[Session, Lead, datetime], None]:
    """Deps.handoff port: send the summary as a "handoff" alert (the alerter writes the Alert row in
    the caller's transaction and posts to Teams after commit)."""
    def _handoff(session: Session, lead: Lead, now: datetime) -> None:
        alerter.send("handoff", build_handoff_summary(session, lead), lead, now, session)
        logger.info("lead %s: handoff summary sent", lead.id)

    return _handoff
