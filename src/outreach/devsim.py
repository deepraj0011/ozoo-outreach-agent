"""Signed webhook requests for local testing (`python -m outreach.dev sim ...` and
tests/test_local_e2e.py share these builders).

Each builder returns a SimRequest (method, path, headers, body) signed with the same secrets as
the server, in the payload shapes of the parser tests' fixtures (tests/fixtures/*.json):

  email-*   Instantly webhook (X-Ozoo-Webhook-Secret)          -> POST /webhooks/email
  click     our tracked link /r/<token> with a browser or scanner user agent
  wa-*      Meta WhatsApp Cloud API webhook (X-Hub-Signature-256) -> POST /webhooks/whatsapp
  cal-*     Cal.com webhook (X-Cal-Signature-256)              -> POST /webhooks/cal
  teams     Teams outgoing webhook (Authorization: HMAC ...)    -> POST /teams/commands

Times come from `now` (the server's clock: the local fake clock when APP_ENV=local).
"""
from __future__ import annotations

import html

import re

import base64
import hashlib
import hmac
import json
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from .booking import SIGNATURE_HEADER as CAL_SIGNATURE_HEADER
from .config import Settings
from .emailtool.instantly import SECRET_HEADER as EMAIL_SECRET_HEADER
from .links import lead_token, make_link, visit_token
from .whatsapp.webhook import SIGNATURE_HEADER as WA_SIGNATURE_HEADER

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
              "Version/17.0 Safari/605.1.15")
SCANNER_UA = "Mozilla/5.0 (compatible; Barracuda Sentinel)"
WA_BUTTONS = {"book_demo": ("BOOK_DEMO", "Book a demo"), "ask_question": ("ASK_QUESTION", "Ask a question")}
BUSINESS_NUMBER = "15550000000"  # Meta's display number in the fixtures


@dataclass
class SimRequest:
    method: str
    path: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass
class SimLead:
    """What the builders need to know about a lead."""

    id: int
    email: Optional[str] = None
    phone: Optional[str] = None
    timezone_group: Optional[str] = None
    step_sent: int = 0
    active_channel: Optional[str] = None

    @classmethod
    def of(cls, lead: Any) -> "SimLead":
        return cls(id=lead.id, email=lead.email, phone=lead.phone, timezone_group=lead.timezone_group,
                   step_sent=lead.step_sent or 0, active_channel=lead.active_channel)


def _iso_ms(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _dumps(data: Any) -> bytes:
    return json.dumps(data, separators=(",", ":")).encode()


def _json_headers(**extra: str) -> Dict[str, str]:
    return {"Content-Type": "application/json", **extra}


# ====================================================================== email (Instantly)

def _campaign(settings: Settings, lead: SimLead) -> str:
    group = lead.timezone_group or settings.default_tz_group
    return settings.campaign_ids.get(group) or next(iter(settings.campaign_ids.values()), "campaign")


def email_event(settings: Settings, lead: SimLead, now: datetime, event_type: str, *, step: Optional[int] = None,
                **extra: Any) -> SimRequest:
    """Shape of tests/fixtures/tool_*.json. The lead is matched by lead_email."""
    if not lead.email:
        raise ValueError(f"lead {lead.id} has no email")
    payload: Dict[str, Any] = {
        "event_type": event_type,
        "timestamp": _iso_ms(now),
        "workspace": "00000000-0000-0000-0000-000000000001",
        "campaign_id": _campaign(settings, lead),
        "campaign_name": "ozoo-local",
        "lead_email": lead.email,
        "email_account": "sender@mail.example.com",
        "unibox_url": "https://app.instantly.ai/app/unibox",
        "step": step if step is not None else max(lead.step_sent, 1),
        "variant": 1,
        "email_id": str(uuid.uuid4()),
        **extra,
    }
    return SimRequest("POST", "/webhooks/email", _json_headers(**{EMAIL_SECRET_HEADER: settings.tool_webhook_secret}),
                      _dumps(payload))


def email_sent(settings: Settings, lead: SimLead, now: datetime, *, step: int = 1,
               minutes_ago: float = 10) -> SimRequest:
    """Email `step` went out `minutes_ago` minutes ago (default 10: a click right after is not
    inside the 60 s bot window)."""
    return email_event(settings, lead, now - timedelta(minutes=minutes_ago), "email_sent", step=step,
                       email_subject=f"Step {step}", email_html="<p>Hi</p>")


def email_reply(settings: Settings, lead: SimLead, now: datetime, *, text: str,
                step: Optional[int] = None) -> SimRequest:
    return email_event(settings, lead, now, "reply_received", step=step, reply_subject="Re: Quick question",
                       reply_text=text, reply_text_snippet=text[:50], reply_html=f"<p>{text}</p>")


def email_bounce(settings: Settings, lead: SimLead, now: datetime, *, hard: bool = True) -> SimRequest:
    return email_event(settings, lead, now, "email_bounced", bounce_type="hard" if hard else "soft")


def email_unsub(settings: Settings, lead: SimLead, now: datetime) -> SimRequest:
    return email_event(settings, lead, now, "lead_unsubscribed")


def email_open(settings: Settings, lead: SimLead, now: datetime, *, ip: str = "203.0.113.7",
               user_agent: str = BROWSER_UA) -> SimRequest:
    """ip 17.x.x.x = Apple Mail Privacy Protection proxy (verdict machine)."""
    return email_event(settings, lead, now, "email_opened", ip=ip, user_agent=user_agent)


# ====================================================================== tracked link

def click(settings: Settings, lead: SimLead, *, tag: str, step: Optional[int] = None,
          channel: Optional[str] = None, scanner: bool = False) -> SimRequest:
    ch = channel or (lead.active_channel if lead.active_channel in ("email", "whatsapp") else "email")
    url = make_link(lead.id, step if step is not None else lead.step_sent, tag, ch, settings=settings)
    return SimRequest("GET", urlsplit(url).path, {"User-Agent": SCANNER_UA if scanner else BROWSER_UA})


_CONFIRM_RE = re.compile(r'fetch\("(/r/[A-Za-z0-9_.\-]+/ok\?e=\d+)"')
_HREF_RE = re.compile(r'<a href="([^"]+)"')


def click_confirm(page: str, user_agent: str = BROWSER_UA) -> Optional[SimRequest]:
    """What a person's browser does on our click page (CLICK_JS_CONFIRM): the script's beacon. None
    when the answer was a plain redirect. A scanner never sends it."""
    m = _CONFIRM_RE.search(page or "")
    return SimRequest("POST", m.group(1), {"User-Agent": user_agent}) if m else None


def click_target(page: str) -> Optional[str]:
    m = _HREF_RE.search(page or "")
    return html.unescape(m.group(1)) if m else None


def visit(settings: Settings, lead: SimLead, *, tag: str, step: Optional[int] = None,
          channel: Optional[str] = None, active_s: int = 30, scroll_pct: int = 60, pages: int = 1,
          interactions: int = 8, user_agent: str = BROWSER_UA,
          headers: Optional[Dict[str, str]] = None, now: Optional[datetime] = None) -> SimRequest:
    """What the website's /v.js snippet sends after a person read the page (outreach.visits). The
    server stores it only when engaged (15 s active + scroll 40 % or 3 interactions, or 2 pages)."""
    ch = channel or (lead.active_channel if lead.active_channel in ("email", "whatsapp") else "email")
    token = visit_token(lead.id, step if step is not None else max(lead.step_sent, 1), tag, ch, settings=settings,
                        issued_at=now)
    body = _dumps({"a": active_s, "s": scroll_pct, "p": pages, "i": interactions})
    return SimRequest("POST", f"/v/{token}", {"Content-Type": "text/plain", "User-Agent": user_agent,
                                              **(headers or {})}, body)


# ====================================================================== WhatsApp (Meta)

def _digits(lead: SimLead) -> str:
    if not lead.phone:
        raise ValueError(f"lead {lead.id} has no phone")
    return "".join(c for c in lead.phone if c.isdigit())


def _wa_envelope(value: Dict[str, Any]) -> Dict[str, Any]:
    """Shape of tests/fixtures/wa_*.json."""
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA_ID", "changes": [{
        "field": "messages",
        "value": {"messaging_product": "whatsapp",
                  "metadata": {"display_phone_number": BUSINESS_NUMBER, "phone_number_id": "PHONE_NUMBER_ID"},
                  **value}}]}]}


def _wa_request(settings: Settings, data: Dict[str, Any]) -> SimRequest:
    body = _dumps(data)
    sig = "sha256=" + hmac.new(settings.wa_app_secret.encode(), body, hashlib.sha256).hexdigest()
    return SimRequest("POST", "/webhooks/whatsapp", _json_headers(**{WA_SIGNATURE_HEADER: sig}), body)


def _wa_message(settings: Settings, lead: SimLead, now: datetime, msg: Dict[str, Any]) -> SimRequest:
    frm = _digits(lead)
    message = {"from": frm, "id": f"wamid.SIM{uuid.uuid4().hex}", "timestamp": str(int(now.timestamp())), **msg}
    return _wa_request(settings, _wa_envelope({
        "contacts": [{"profile": {"name": "Lead"}, "wa_id": frm}], "messages": [message]}))


def wa_button(settings: Settings, lead: SimLead, now: datetime, *, button: str) -> SimRequest:
    """button: book_demo | ask_question (quick-reply payloads BOOK_DEMO / ASK_QUESTION)."""
    if button not in WA_BUTTONS:
        raise ValueError(f"button must be one of {', '.join(WA_BUTTONS)}")
    payload, label = WA_BUTTONS[button]
    return _wa_message(settings, lead, now, {"type": "button", "button": {"payload": payload, "text": label}})


def wa_text(settings: Settings, lead: SimLead, now: datetime, *, text: str) -> SimRequest:
    return _wa_message(settings, lead, now, {"type": "text", "text": {"body": text}})


def wa_status(settings: Settings, lead: SimLead, now: datetime, *, status: str, code: Optional[int] = None,
              wamid: Optional[str] = None) -> SimRequest:
    """status: sent | delivered | read | failed (failed carries errors[0].code, e.g. 131026)."""
    st: Dict[str, Any] = {"id": wamid or f"wamid.SIMOUT{uuid.uuid4().hex}", "status": status,
                          "timestamp": str(int(now.timestamp())), "recipient_id": _digits(lead)}
    if status == "failed":
        st["errors"] = [{"code": code if code is not None else 131026, "title": "Message undeliverable",
                         "message": "Message undeliverable", "error_data": {"details": "Message Undeliverable."}}]
    return _wa_request(settings, _wa_envelope({"statuses": [st]}))


# ====================================================================== Cal.com

CAL_TRIGGERS = {
    "cal-booked": "BOOKING_CREATED",
    "cal-cancelled": "BOOKING_CANCELLED",
    "cal-rescheduled": "BOOKING_RESCHEDULED",
    "cal-no-show": "BOOKING_NO_SHOW_UPDATED",
    "cal-meeting-ended": "MEETING_ENDED",
    "cal-form": "FORM_SUBMITTED",
}


def _attendee_email(lead: SimLead) -> str:
    return lead.email or f"lead{lead.id}@example.com"


def _cal_request(settings: Settings, data: Dict[str, Any]) -> SimRequest:
    body = _dumps(data)
    sig = hmac.new(settings.cal_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return SimRequest("POST", "/webhooks/cal", _json_headers(**{CAL_SIGNATURE_HEADER: sig}), body)


def _booking_body(settings: Settings, lead: SimLead, uid: str, start: datetime) -> Dict[str, Any]:
    return {
        "title": "Ozoo demo",
        "startTime": _iso_ms(start),
        "endTime": _iso_ms(start + timedelta(minutes=30)),
        "organizer": {"name": "Ozoo", "email": "sales@example.com", "timeZone": settings.business_tz},
        "attendees": [{"name": "Lead", "email": _attendee_email(lead), "timeZone": settings.business_tz}],
        "uid": uid,
        "bookingId": zlib.crc32(uid.encode()) % 10**8,
        "status": "ACCEPTED",
        "metadata": {"lead": lead_token(lead.id, settings=settings)},
    }


def cal_event(settings: Settings, lead: SimLead, now: datetime, scenario: str, *, uid: Optional[str] = None,
              start: Optional[datetime] = None, size: Optional[str] = None,
              timeline: Optional[str] = None) -> SimRequest:
    """Shapes of tests/fixtures/cal_*.json; the lead is matched by the signed metadata.lead token
    (what our booking redirect adds). Booking times: start tomorrow (rescheduled: the day after),
    meeting-ended: a meeting that just ended."""
    trigger = CAL_TRIGGERS.get(scenario)
    if trigger is None:
        raise ValueError(f"unknown cal scenario {scenario}")
    uid = uid or f"sim-{lead.id}"
    if trigger == "FORM_SUBMITTED":
        responses: Dict[str, Any] = {"email": {"label": "Email", "value": _attendee_email(lead),
                                               "response": _attendee_email(lead)}}
        if size is not None:
            responses["company_size"] = {"label": "Company size", "value": size, "response": size}
        if timeline is not None:
            responses["timeline"] = {"label": "Timeline", "value": timeline, "response": timeline}
        return _cal_request(settings, {"triggerEvent": trigger, "createdAt": _iso_ms(now), "payload": {
            "formId": "form_local", "formName": "Ozoo routing", "responses": responses,
            "responseId": uuid.uuid4().hex[:12], "metadata": {"lead": lead_token(lead.id, settings=settings)}}})
    if trigger == "BOOKING_NO_SHOW_UPDATED":
        return _cal_request(settings, {"triggerEvent": trigger, "createdAt": _iso_ms(now), "payload": {
            "message": "attendee marked as no-show",
            "attendees": [{"email": _attendee_email(lead), "noShow": True}],
            "bookingUid": uid, "bookingId": zlib.crc32(uid.encode()) % 10**8,
            "metadata": {"lead": lead_token(lead.id, settings=settings)}}})
    if trigger == "MEETING_ENDED":  # flat shape, no payload wrapper (cal_meeting_ended.json)
        body = _booking_body(settings, lead, uid, start or now - timedelta(minutes=30))
        return _cal_request(settings, {"triggerEvent": trigger, **body})
    default_start = (now + timedelta(days=2 if trigger == "BOOKING_RESCHEDULED" else 1)).replace(
        minute=0, second=0, microsecond=0)
    return _cal_request(settings, {"triggerEvent": trigger, "createdAt": _iso_ms(now),
                                   "payload": _booking_body(settings, lead, uid, start or default_start)})


# ====================================================================== Teams outgoing webhook

def teams_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z"  # Teams: 7 digits


def teams_command(settings: Settings, now: datetime, *, text: str, aad: Optional[str] = None,
                  name: str = "Local Salesperson") -> SimRequest:
    """An @mention in the sales channel, from the first allow-listed user (TEAMS_ALLOWED_AAD_IDS)."""
    tenant = settings.teams_tenant_id
    channel = settings.teams_sales_channel_id
    user = aad or sorted(settings.teams_allowed_aad_id_set)[0]
    activity = {
        "type": "message",
        "id": f"sim{uuid.uuid4().hex[:20]}",
        "timestamp": teams_ts(now),
        "serviceUrl": "https://smba.trafficmanager.net/emea/",
        "channelId": "msteams",
        "from": {"id": "29:local", "name": name, "aadObjectId": user},
        "conversation": {"isGroup": True, "id": f"{channel};messageid=1700000000001",
                         "conversationType": "channel", "tenantId": tenant},
        "recipient": None,
        "textFormat": "plain",
        "text": f"<at>Ozoo Outreach</at> {text}",
        "channelData": {"tenant": {"id": tenant}, "teamsTeamId": "19:team@thread.tacv2",
                        "channel": {"id": channel}},
    }
    body = _dumps(activity)
    sig = base64.b64encode(hmac.new(settings.teams_outgoing_key, body, hashlib.sha256).digest()).decode()
    return SimRequest("POST", "/teams/commands", _json_headers(Authorization=f"HMAC {sig}"), body)


def unsubscribe_link(settings: Settings, lead: SimLead, *, step: Optional[int] = None,
                     channel: str = "email") -> SimRequest:
    """The confirm button on our unsubscribe page (also what RFC 8058 one-click POSTs)."""
    url = make_link(lead.id, step if step is not None else lead.step_sent, "unsubscribe", channel, settings=settings)
    token = urlsplit(url).path.rsplit("/", 1)[-1]
    return SimRequest("POST", f"/u/{token}", {"User-Agent": BROWSER_UA})
