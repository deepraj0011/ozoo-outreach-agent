"""Local demo console (APP_ENV=local only): a live web view of the agent at /demo.

Read side: leads, pipeline counts, a live activity feed, one lead's timeline / WhatsApp chat / alerts.
Act side: buttons that "play the outside world" by running the same `python -m outreach.dev` commands
the testing guide uses (seed, tick, advance, sim ...). Each action is a fixed argv built from a
whitelist (no shell), so the page can only do what the CLI already does locally.

Never registered in production (create_app checks APP_ENV). Shows contact details on purpose: it is
local sample data, like `dev show`.
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from .models import Alert, ChatMessage, Event, Job, Lead
from .score import band
from .states import LeadState
from .whatsapp.sender import CTA_KEYS, VARIABLE_FALLBACK, load_wa_templates, template_values

import csv
import io
import re

PAGE = Path(__file__).with_name("demo_console.html")
LOOPBACK_CLIENTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})
# Host / Origin names the console answers to: a page on another site that re-points its own DNS
# name at 127.0.0.1 (DNS rebinding) still sends its own name, so it is refused ("testserver" is the
# test client's default, not a real name).
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "testserver"})


def _host_name(value: str) -> str:
    """"127.0.0.1:8000" / "[::1]:8000" / "http://localhost:8000" -> the bare host name."""
    v = value.strip().lower().split("://", 1)[-1].split("/", 1)[0]
    if v.startswith("["):
        return v[1:v.find("]")] if "]" in v else v
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def is_local_request(request: Request) -> bool:
    client = request.client.host if request.client else ""
    if any(h in request.headers for h in TUNNEL_HEADERS) or client not in LOOPBACK_CLIENTS:
        return False
    if _host_name(request.headers.get("host", "")) not in LOCAL_HOSTS:
        return False
    origin = request.headers.get("origin")
    return origin is None or _host_name(origin) in LOCAL_HOSTS
TUNNEL_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "x-forwarded-host", "x-real-ip",
                  "forwarded", "ngrok-skip-browser-warning")
IMPORT_MAX_BYTES = 5 * 1024 * 1024
IMPORT_MAX_ROWS = 2000
# lead field -> accepted column headers (lower-case, spaces/underscores/dashes removed)
HEADER_ALIASES: Dict[str, tuple] = {
    "name": ("name", "fullname", "contactname", "leadname"),
    "first_name": ("firstname", "first", "givenname"),
    "last_name": ("lastname", "last", "surname", "familyname"),
    "company": ("company", "companyname", "organisation", "organization", "account", "business"),
    "role": ("role", "title", "jobtitle", "designation", "position"),
    "email": ("email", "emailaddress", "mail", "workemail"),
    "phone": ("phone", "phonenumber", "mobile", "mobilenumber", "whatsapp", "whatsappnumber", "contactnumber"),
    "country": ("country", "countrycode"),
    "consent_whatsapp": ("consentwhatsapp", "whatsappconsent", "whatsappoptin", "optin", "consent",
                         "whatsappoptedin"),
    "industry": ("industry", "sector"),
    "size": ("size", "companysize", "employees"),
}
_YES = {"yes", "y", "true", "1", "opted in", "optin", "opt-in", "x"}
FEED_ROWS = 60
LEAD_ROWS = 200
RUN_TIMEOUT_S = 30
TEXT_MAX = 1000

# pipeline order shown on the page
STATE_ORDER = [s.value for s in (
    LeadState.INTAKE, LeadState.IN_SEQUENCE, LeadState.PAUSED, LeadState.ENGAGED, LeadState.QUALIFYING,
    LeadState.BOOKED, LeadState.HANDED_OFF, LeadState.NURTURE, LeadState.CLOSED, LeadState.SUPPRESSED,
    LeadState.REJECTED, LeadState.ARCHIVED)]

# action -> (needs lead, needs text, builder of the dev CLI argv after "python -m outreach.dev")
_SIM = "sim"


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _event_summary(e: Event) -> str:
    d = e.data or {}
    if e.kind == "state_change":
        return f"{d.get('from')} → {d.get('to')}" + (f" ({d.get('reason')})" if d.get("reason") else "")
    parts: List[str] = []
    cls = d.get("classification")
    if isinstance(cls, dict) and cls.get("label"):
        parts.append(f"AI: {cls['label']}")
    for key in ("text", "payload", "code", "fit"):
        v = d.get(key)
        if v not in (None, ""):
            s = str(v)
            parts.append(s if len(s) <= 90 else s[:89] + "…")
    if e.tag:
        parts.append(f"link: {e.tag}")
    if e.step is not None:
        parts.append(f"step {e.step}")
    return " · ".join(parts)


def _lead_row(ld: Lead, p0_counts: Optional[Dict[int, Tuple[int, int]]] = None) -> Dict[str, Any]:
    """p0_counts (P0 leads): {lead id: (emails sent, emails scheduled)} shown instead of "step of 4"."""
    sent, total = (p0_counts or {}).get(ld.id, (ld.step_sent, 4))
    return {
        "id": ld.id, "name": ld.name, "company": ld.company, "role": ld.role, "email": ld.email,
        "phone": ld.phone, "state": ld.state, "channel": ld.active_channel,
        "switched": bool(ld.channel_switched), "score": ld.score, "band": band(ld.score),
        "step_sent": ld.step_sent, "emails_sent": sent, "emails_total": total,
        "pause_reason": ld.pause_reason, "email_hold": ld.email_hold,
        "is_eu": bool(ld.is_eu), "consent_whatsapp": bool(ld.consent_whatsapp),
        "human_owner": ld.wa_human_owner_name or ld.wa_human_owner, "bot_turns": ld.wa_bot_turns,
        "nurture_track": ld.nurture_track, "nurture_until": _iso(ld.nurture_until),
        "last_sent_at": _iso(ld.last_sent_at), "created_at": _iso(ld.created_at),
    }


def _p0_counts(session: Any, leads: List[Lead]) -> Dict[int, Tuple[int, int]]:
    from .p0.planner import email_counts  # p0 imports this package's modules
    return email_counts(session, [ld.id for ld in leads if (ld.variant_flags or {}).get("p0")])


# ------------------------------------------------------------------ WhatsApp phone view
def _template_view(name: str, lead: Any, templates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A Meta template as the lead's phone shows it, from the local preview copy in whatsapp.yaml."""
    cfg = templates["templates"].get(name) or {}
    pv = cfg.get("preview")
    if not pv:
        return None
    vals = template_values(lead)
    text = pv["body"]
    for i, var in enumerate(cfg.get("variables", []), start=1):
        text = text.replace("{{%d}}" % i, vals.get(var) or VARIABLE_FALLBACK[var])
    buttons = [{"label": label, "payload": payload}
               for label, payload in zip(pv.get("buttons", []), cfg.get("quick_replies", []))]
    return {"kind": "template", "name": name, "text": text, "buttons": buttons}


def _cta_texts(lead: Any, templates: Dict[str, Any]) -> Dict[str, str]:
    """Rendered text of each message sent with a link button -> the button label."""
    vals = {**template_values(lead), "booking_link": ""}
    out = {}
    for key in CTA_KEYS:
        tpl = templates["texts"].get(key, "")
        if "{booking_link}" not in tpl:
            out[tpl.format_map(vals)] = templates["buttons"][key]
    return out


def chat_view(lead: Any, chat: List[Any], templates: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Chat rows plus `view`: what the lead's phone shows (template text + quick-reply buttons,
    link buttons), since templates are stored only as "[template:name]"."""
    ctas = _cta_texts(lead, templates)
    rows = []
    for m in chat:
        text = m.text or ""
        view: Optional[Dict[str, Any]] = None
        if m.direction == "out" and text.startswith("[template:") and text.endswith("]"):
            view = _template_view(text[10:-1], lead, templates)
        elif m.direction == "out" and text in ctas:
            view = {"kind": "cta", "text": text, "cta": ctas[text]}
        rows.append({"id": m.id, "direction": m.direction, "author": m.author, "text": text,
                     "at": _iso(m.created_at), "view": view or {"kind": "text", "text": text}})
    return rows


def wa_preview(lead: Any, templates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The intro the lead will get, shown before anything is sent; None without a phone."""
    if not lead.phone:
        return None
    message = _template_view("ozoo_intro", lead, templates)
    if not lead.consent_whatsapp:
        return {"will_send": False, "why": "No WhatsApp opt-in: the agent never messages this number.", "message": message}
    if lead.active_channel != "whatsapp":
        return {"will_send": False, "why": "This lead is on email. WhatsApp starts only if email gets no response.",
                "message": message}
    if lead.state not in (LeadState.IN_SEQUENCE.value,):
        return {"will_send": False, "why": "This lead is no longer in the outreach sequence.", "message": message}
    return {"will_send": True, "why": "Sent automatically at the next worker tick (within seconds).", "message": message}


def _event_row(e: Event, names: Dict[int, str]) -> Dict[str, Any]:
    return {"id": e.id, "lead_id": e.lead_id, "lead": names.get(e.lead_id or -1), "channel": e.channel,
            "kind": e.kind, "verdict": e.verdict, "at": _iso(e.occurred_at), "summary": _event_summary(e)}


def build_argv(body: Dict[str, Any]) -> List[str]:
    """Turn a page action into dev CLI arguments. Raises ValueError for anything not whitelisted."""
    action = body.get("action")
    lead = body.get("lead")
    text = body.get("text")
    if lead is not None:
        if isinstance(lead, bool) or not isinstance(lead, int) or not 0 < lead < 10**9:
            raise ValueError("bad lead id")
    if text is not None:
        if not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX:
            raise ValueError("bad text")
        text = text.strip()

    def need_lead() -> str:
        if lead is None:
            raise ValueError("pick a lead first")
        return str(lead)

    def need_text() -> str:
        if text is None:
            raise ValueError("type a message first")
        return text

    if action == "seed":
        return ["seed"]
    if action == "tick":
        return ["tick"]
    if action == "advance":
        hours = body.get("hours")
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 < hours <= 24 * 60:
            raise ValueError("bad hours")
        return ["advance", "--hours", str(hours)]
    if action == "reset_clock":
        return ["advance", "--reset"]
    if action == "email_sent":
        step = body.get("step", 1)
        if step not in (1, 2, 3, 4):
            raise ValueError("bad step")
        return [_SIM, "email-sent", "--lead", need_lead(), "--step", str(step)]
    if action == "email_reply":
        return [_SIM, "email-reply", "--lead", need_lead(), "--text", need_text()]
    if action == "email_bounce":
        return [_SIM, "email-bounce", "--lead", need_lead(), "--hard"]
    if action == "click":
        tag = body.get("tag")
        if tag not in ("pricing", "case_study", "product", "voice", "chat", "trial", "homepage", "booking"):
            raise ValueError("bad link")
        return [_SIM, "click", "--lead", need_lead(), "--tag", tag]
    if action == "scanner_click":
        return [_SIM, "click", "--lead", need_lead(), "--tag", "pricing", "--scanner"]
    if action == "unsubscribe":
        return [_SIM, "unsub-link", "--lead", need_lead()]
    if action == "wa_button":
        button = body.get("button")
        if button not in ("book_demo", "ask_question"):
            raise ValueError("bad button")
        return [_SIM, "wa-button", "--lead", need_lead(), "--id", button]
    if action == "wa_text":
        return [_SIM, "wa-text", "--lead", need_lead(), "--text", need_text()]
    if action == "wa_not_on_whatsapp":
        return [_SIM, "wa-status", "--lead", need_lead(), "--status", "failed", "--code", "131026"]
    if action == "cal_booked":
        return [_SIM, "cal-booked", "--lead", need_lead()]
    if action == "cal_meeting_ended":
        return [_SIM, "cal-meeting-ended", "--lead", need_lead()]
    if action == "teams_wa":
        return [_SIM, "teams", "--text", f"wa {need_lead()} {need_text()}"]
    if action == "teams_done":
        return [_SIM, "teams", "--text", f"done {need_lead()}"]
    if action == "teams_nurture":
        return [_SIM, "teams", "--text", f"nurture {need_lead()}"]
    raise ValueError("unknown action")


async def _run_cli(argv: List[str], base_url: str) -> Dict[str, Any]:
    cmd = [sys.executable, "-m", "outreach.dev"]
    env_file = os.environ.get("OUTREACH_ENV_FILE")
    if env_file:
        cmd += ["--env-file", env_file]
    cmd += argv
    if argv and argv[0] == _SIM:
        cmd += ["--base-url", base_url]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=RUN_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        return {"ok": False, "output": "timed out"}
    return {"ok": proc.returncode == 0, "output": out.decode("utf-8", "replace")[-4000:]}


def _norm_header(h: Any) -> str:
    return re.sub(r"[\s_\-\.]+", "", str(h or "").strip().lower())


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)  # Excel stores phone numbers as numbers
    return str(v).strip()


def read_table(filename: str, data: bytes) -> List[List[str]]:
    """Rows of cells from a .csv or .xlsx file (first sheet). Raises ValueError with a readable message."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
        except ImportError:
            raise ValueError("Excel support is not installed (pip install openpyxl); upload a CSV instead")
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:  # noqa: BLE001 - any broken workbook
            raise ValueError("could not read this Excel file; save it as .xlsx or CSV and try again")
        ws = wb.worksheets[0]
        rows = [[_cell(c) for c in row] for row in ws.iter_rows(values_only=True)]
        wb.close()
        return rows
    if name.endswith((".csv", ".txt")):
        text = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    if name.endswith(".xls"):
        raise ValueError("old .xls files are not supported; in Excel use Save As -> .xlsx or CSV")
    raise ValueError("upload a .csv or .xlsx file")


def parse_leads(filename: str, data: bytes) -> Dict[str, Any]:
    """Map the file's columns to lead fields and build one intake payload per row."""
    rows = [r for r in read_table(filename, data) if any(c for c in r)]
    if not rows:
        raise ValueError("the file is empty")
    header = [_norm_header(h) for h in rows[0]]
    mapping: Dict[str, int] = {}
    for field, aliases in HEADER_ALIASES.items():
        for i, h in enumerate(header):
            if h in aliases and field not in mapping:
                mapping[field] = i
    if "name" not in mapping and "first_name" not in mapping:
        raise ValueError("no name column found (expected a column called Name, or First name / Last name)")
    if "email" not in mapping and "phone" not in mapping:
        raise ValueError("no Email or Phone / WhatsApp column found")
    body = rows[1:]
    if len(body) > IMPORT_MAX_ROWS:
        raise ValueError(f"too many rows ({len(body)}); the limit is {IMPORT_MAX_ROWS}")

    def get(row: List[str], field: str) -> str:
        i = mapping.get(field)
        return row[i].strip() if i is not None and i < len(row) else ""

    leads: List[Dict[str, Any]] = []
    for n, row in enumerate(body, start=2):
        name = get(row, "name") or " ".join(x for x in (get(row, "first_name"), get(row, "last_name")) if x)
        payload: Dict[str, Any] = {"name": name, "company": get(row, "company"),
                                   "role": get(row, "role") or "Not given", "source": "file_import"}
        email = get(row, "email")
        phone = get(row, "phone")
        if phone and phone[0] in "123456789" and re.fullmatch(r"\d{11,15}", phone):
            phone = "+" + phone  # international number stored without the plus (common in Excel)
        if email:
            payload["email"] = email
        if phone:
            payload["phone"] = phone
        for f in ("country", "industry", "size"):
            if get(row, f):
                payload[f] = get(row, f)
        payload["consent_whatsapp"] = get(row, "consent_whatsapp").lower() in _YES
        problems = []
        if not name:
            problems.append("no name")
        if not payload["company"]:
            problems.append("no company")
        if not email and not phone:
            problems.append("no email or phone")
        if phone and not email and not payload["consent_whatsapp"]:
            problems.append("phone only, but no WhatsApp opt-in")
        leads.append({"row": n, "payload": payload, "problems": problems})
    found = {f: rows[0][i] for f, i in mapping.items()}
    return {"columns": found, "leads": leads}


def _import_plan(start: Optional[str], settings: Any) -> Dict[str, Any]:
    """The email-1 date and time picked in the import dialog (local, BUSINESS_TZ), as an intake field."""
    from .send_plan import PlanError, _local_at, local_to_utc

    if not start:
        return {}
    try:
        return {"plan_start": local_to_utc(_local_at("Email 1", start), settings).isoformat()}
    except PlanError as exc:
        raise ValueError(str(exc)) from None


def register(app: FastAPI, session_factory: sessionmaker, clock: Callable[[], datetime], settings: Any) -> None:
    modes = {"email": settings.email_mode, "whatsapp": settings.whatsapp_mode, "ai": settings.llm_mode,
             "alerts": settings.alerts_mode}

    @app.middleware("http")
    async def console_is_local_only(request: Request, call_next: Any) -> Response:
        """The console shows lead data and runs dev actions, so it answers only on this computer.
        A request that came through a tunnel (cloudflared, ngrok, ...) carries a forwarding header
        even though it arrives from 127.0.0.1: those get a 404. The web app (/app) is covered too.
        Tracked links (/r/), webhooks and unsubscribe pages are not affected."""
        path = request.url.path
        console = path in ("/", "/demo", "/app") or path.startswith(("/demo/", "/app/"))
        if console and not is_local_request(request):
            return Response(status_code=404)
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    def home() -> Response:
        return Response(status_code=307, headers={"Location": "/app/"})

    @app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
    def demo_page() -> HTMLResponse:
        return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @app.get("/demo/api/state", include_in_schema=False)
    def demo_state() -> JSONResponse:
        with session_factory() as s:
            leads = list(s.scalars(select(Lead).order_by(Lead.id.desc()).limit(LEAD_ROWS)))
            names = {ld.id: ld.name for ld in leads}
            p0_counts = _p0_counts(s, leads)
            events = list(s.scalars(select(Event).order_by(Event.id.desc()).limit(FEED_ROWS)))
            alerts = list(s.scalars(select(Alert).order_by(Alert.id.desc()).limit(50)))
            counts = Counter(ld.state for ld in leads)
            ev_kinds = Counter(s.scalars(select(Event.kind)))
            wa_msgs = len(list(s.scalars(select(ChatMessage.id))))
            # which states each lead ever reached (for the funnel), last activity, last chat line
            reached: Dict[str, set] = defaultdict(set)
            for lid, data in s.execute(select(Event.lead_id, Event.data).where(Event.kind == "state_change")):
                if isinstance(data, dict) and data.get("to"):
                    reached[data["to"]].add(lid)
            replied = set(s.scalars(select(Event.lead_id).where(
                Event.kind.in_(("reply", "wa_text", "wa_button"))).distinct()))
            last_at = dict(s.execute(select(Event.lead_id, func.max(Event.occurred_at)).group_by(Event.lead_id)).all())
            last_msg: Dict[int, Dict[str, Any]] = {}
            for m in s.scalars(select(ChatMessage).order_by(ChatMessage.id)):
                last_msg[m.lead_id] = {"text": m.text, "author": m.author, "at": _iso(m.created_at)}
        for ld in leads:
            reached[ld.state].add(ld.id)

        def ever(*states: str) -> int:
            return len(set().union(*(reached.get(x, set()) for x in states)))

        contacted = {ld.id for ld in leads if ld.step_sent or ld.wa_template_sent_at or ld.last_sent_at
                     or ld.tool_lead_id}
        interested = set().union(*(reached.get(x, set()) for x in ("engaged", "qualifying", "booked", "handed_off")))
        replied |= interested  # a booking or a button tap is a response too: the funnel never widens
        funnel = [
            {"label": "Leads in", "count": len(leads)},
            {"label": "Passed intake", "count": ever("in_sequence", "paused", "engaged", "qualifying", "booked",
                                                     "handed_off", "nurture", "closed", "archived")},
            {"label": "Contacted", "count": len(contacted)},
            {"label": "Replied", "count": len(replied)},
            {"label": "Interested", "count": len(interested)},
            {"label": "Meeting booked", "count": ever("booked", "handed_off")},
            {"label": "Handed to sales", "count": ever("handed_off")},
        ]
        rows = []
        for ld in leads:
            r = _lead_row(ld, p0_counts)
            r["last_at"] = _iso(last_at.get(ld.id))
            r["last_msg"] = last_msg.get(ld.id)
            rows.append(r)
        return JSONResponse({
            "clock": _iso(clock()), "modes": modes, "funnel": funnel,
            "pipeline": [{"state": st, "count": counts.get(st, 0)} for st in STATE_ORDER],
            "totals": {
                "leads": len(leads),
                "emails_sent": ev_kinds.get("sent", 0),
                "replies": ev_kinds.get("reply", 0) + ev_kinds.get("wa_text", 0) + ev_kinds.get("wa_button", 0),
                "wa_messages": wa_msgs,
                "booked": counts.get("booked", 0) + counts.get("handed_off", 0),
                "alerts": len(alerts),
            },
            "leads": rows,
            "feed": [_event_row(e, names) for e in events],
            "alerts": [{"id": a.id, "lead_id": a.lead_id, "lead": names.get(a.lead_id or -1), "kind": a.kind,
                        "text": a.text, "at": _iso(a.created_at)} for a in alerts],
        }, headers={"Cache-Control": "no-store"})

    @app.get("/demo/api/lead/{lead_id}", include_in_schema=False)
    def demo_lead(lead_id: int) -> JSONResponse:
        with session_factory() as s:
            ld = s.get(Lead, lead_id)
            if ld is None:
                return JSONResponse({"error": "not found"}, status_code=404)
            names = {ld.id: ld.name}
            wa_templates = load_wa_templates()
            events = list(s.scalars(select(Event).where(Event.lead_id == lead_id).order_by(Event.id)))
            chat = list(s.scalars(select(ChatMessage).where(ChatMessage.lead_id == lead_id)
                                  .order_by(ChatMessage.id)))
            alerts = list(s.scalars(select(Alert).where(Alert.lead_id == lead_id).order_by(Alert.id)))
            jobs = list(s.scalars(select(Job).where(Job.lead_id == lead_id).order_by(Job.id)))
            return JSONResponse({
                "lead": _lead_row(ld, _p0_counts(s, [ld])),
                "events": [_event_row(e, names) for e in events],
                "chat": chat_view(ld, chat, wa_templates),
                "wa_preview": None if chat or ld.wa_template_sent_at else wa_preview(ld, wa_templates),
                "alerts": [{"id": a.id, "kind": a.kind, "text": a.text, "at": _iso(a.created_at)} for a in alerts],
                "jobs": [{"id": j.id, "kind": j.kind, "status": j.status, "attempts": j.attempts} for j in jobs],
            }, headers={"Cache-Control": "no-store"})

    @app.post("/demo/api/run", include_in_schema=False)
    async def demo_run(request: Request) -> JSONResponse:
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("bad body")
            argv = build_argv(body)
        except ValueError as exc:
            return JSONResponse({"ok": False, "output": str(exc)}, status_code=400)
        base = str(request.base_url).rstrip("/")
        return JSONResponse(await _run_cli(argv, base))

    @app.get("/demo/api/sample.csv", include_in_schema=False)
    def demo_sample() -> Response:
        text = ("Name,Company,Role,Email,Phone,Country,WhatsApp opt-in\n"
                "Priya Shah,Northwind,VP Operations,priya@northwind.example.com,,US,no\n"
                "Arjun Mehta,Tata Demo,Head of Sales,,+919800000001,IN,yes\n"
                "Sara Lopez,Globex,COO,sara@globex.example.com,+12015550188,US,yes\n")
        return Response(text, media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="leads_template.csv"'})

    @app.post("/demo/api/import", include_in_schema=False)
    async def demo_import(request: Request) -> JSONResponse:
        """Body = the raw file; header X-Filename. ?commit=1 imports, otherwise a preview."""
        import httpx

        filename = request.headers.get("x-filename", "")
        data = b""
        async for chunk in request.stream():
            data += chunk
            if len(data) > IMPORT_MAX_BYTES:
                return JSONResponse({"error": "file is larger than 5 MB"}, status_code=413)
        try:
            parsed = await asyncio.to_thread(parse_leads, filename, data)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if request.query_params.get("commit") != "1":
            return JSONResponse({"columns": parsed["columns"], "total": len(parsed["leads"]),
                                 "ready": sum(1 for x in parsed["leads"] if not x["problems"]),
                                 "rows": parsed["leads"][:200]})
        try:
            plan = _import_plan(request.query_params.get("start"), settings)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        base = str(request.base_url).rstrip("/")
        results = []
        async with httpx.AsyncClient(timeout=20.0) as http:
            for item in parsed["leads"]:
                item["payload"].update(plan)
                if item["problems"]:
                    results.append({"row": item["row"], "name": item["payload"]["name"], "outcome": "skipped",
                                    "reason": ", ".join(item["problems"])})
                    continue
                r = await http.post(base + "/intake", json=item["payload"],
                                    headers={"x-api-key": settings.intake_api_key})
                try:
                    out = r.json()
                except ValueError:
                    out = {"outcome": "error", "reason": f"HTTP {r.status_code}"}
                results.append({"row": item["row"], "name": item["payload"]["name"], "lead_id": out.get("lead_id"),
                                "outcome": out.get("outcome", "error"), "channel": out.get("channel"),
                                "reason": out.get("reason")})
        await _run_cli(["tick"], base)  # start the new leads now instead of on the next 10 s tick
        summary = Counter(r["outcome"] for r in results)
        return JSONResponse({"summary": dict(summary), "results": results})

    @app.post("/demo/api/intake", include_in_schema=False)
    async def demo_intake(request: Request) -> JSONResponse:
        """Add a lead through the real /intake endpoint (same checks, same API key)."""
        import httpx

        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "bad body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "bad body"}, status_code=400)
        allowed = ("name", "company", "role", "email", "phone", "country", "consent_whatsapp")
        payload = {k: body[k] for k in allowed if k in body}
        payload["source"] = "demo_console"
        base = str(request.base_url).rstrip("/")
        async with httpx.AsyncClient(timeout=15.0) as http:
            r = await http.post(base + "/intake", json=payload, headers={"x-api-key": settings.intake_api_key})
        try:
            out = r.json()
        except ValueError:
            out = {"error": f"HTTP {r.status_code}"}
        return JSONResponse(out, status_code=200 if r.status_code < 500 else 502)
