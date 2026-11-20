"""Local-only EmailTool (EMAIL_MODE=mailpit): emails go over SMTP to Mailpit on this machine
(browser inbox at http://localhost:8025), never to the internet.

It plays the part Instantly plays in production: it keeps each enrolled lead's 4-step sequence
and sends the steps when they are due. State lives in a small SQLite file shared by the server
(which enrols / pauses / stops leads) and `python -m outreach.mailpit_sender` (which sends the
due steps and reports them back as signed `email_sent` webhooks).

Safety: the SMTP host must be a loopback address. Logs carry ids only, never addresses or text.
"""
from __future__ import annotations

import html
import ipaddress
import json
import logging
import re
import smtplib
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional

from .base import NormalizedEvent
from .instantly import parse_instantly_webhook
from ..richmail import with_tracking

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATE_FILE = REPO_ROOT / ".mailpit_tool.db"
DEFAULT_SMTP_HOST = "127.0.0.1"
DEFAULT_SMTP_PORT = 1025
FROM_ADDR = "Ozoo.ai (local test) <outreach@ozoo.local>"
LAST_STEP = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mp_lead (
    tool_lead_id TEXT PRIMARY KEY,
    lead_id INTEGER,
    email TEXT NOT NULL,
    campaign_id TEXT,
    variables TEXT NOT NULL,
    enrolled_at TEXT NOT NULL,
    last_step INTEGER NOT NULL DEFAULT 0,
    notified_step INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    last_subject TEXT,
    last_message_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_mp_lead_email ON mp_lead(email);
CREATE TABLE IF NOT EXISTS mp_blocked (email TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS mp_send (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_lead_id TEXT NOT NULL,
    campaign_id TEXT,
    step INTEGER,
    sent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mp_msgid (
    message_id TEXT PRIMARY KEY,
    tool_lead_id TEXT NOT NULL
);
"""


class MailpitError(Exception):
    pass


_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def body_html(body: str, pixel_url: Optional[str] = None, trap_url: Optional[str] = None) -> str:
    """The plain-text body as simple HTML (escaped, links clickable, line breaks kept), plus our
    1x1 open-tracking image and / or the hidden honeypot link at the end. Only URLs we put in the
    body become links; the pixel and trap URLs are ours (signed, from config), never lead input."""
    out: List[str] = []
    pos = 0
    for m in _URL_RE.finditer(body):
        out.append(html.escape(body[pos:m.start()]))
        raw = m.group(0).rstrip(".,;:!?)")  # "…see https://x/r/abc." -> the link stops before the dot
        url = html.escape(raw, quote=True)
        out.append(f'<a href="{url}">{url}</a>')
        pos = m.start() + len(raw)
    out.append(html.escape(body[pos:]))
    text = "".join(out).replace("\r\n", "\n").replace("\n", "<br>\n")
    return (f'<!doctype html><html><body><div style="font-family:Arial,sans-serif;font-size:14px;'
            f'line-height:1.5">{text}</div>{tracking_extras(pixel_url, trap_url)}</body></html>')


def tracking_extras(pixel_url: Optional[str], trap_url: Optional[str]) -> str:
    """Our 1x1 open-tracking image and / or the hidden honeypot link (ours, signed)."""
    extra = ""
    if trap_url:  # invisible to a reader; security scanners follow every href
        extra += (f'<a href="{html.escape(trap_url, quote=True)}" style="display:none" aria-hidden="true" '
                  f'tabindex="-1">&#8203;</a>')
    if pixel_url:
        extra += (f'<img src="{html.escape(pixel_url, quote=True)}" width="1" height="1" alt="" '
                  f'style="border:0;width:1px;height:1px">')
    return extra


def html_with_pixel(body: str, pixel_url: str) -> str:
    return body_html(body, pixel_url=pixel_url)


def thread_subject(first_subject: str) -> str:
    """Follow-ups go in the same thread as email 1: "Re: <email 1 subject>" (never "Re: Re:")."""
    base = first_subject.strip()
    while base.lower().startswith("re:"):
        base = base[3:].strip()
    return f"Re: {base}"


def set_unsubscribe_headers(msg: EmailMessage, unsubscribe_url: Optional[str]) -> None:
    """RFC 2369 + RFC 8058 one-click unsubscribe. Gmail / Yahoo expect it from bulk senders and
    show their own "Unsubscribe" button instead of the reader reaching for "Report spam"."""
    if unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"


def set_body(msg: EmailMessage, body: str, pixel_url: Optional[str], trap_url: Optional[str] = None,
             html_body: Optional[str] = None) -> None:
    """Plain text always; an HTML alternative: the designed one (task 21), else a simple one when
    there is a pixel or a honeypot link to carry."""
    msg.set_content(body)
    if html_body:
        msg.add_alternative(with_tracking(html_body, tracking_extras(pixel_url, trap_url)), subtype="html")
    elif pixel_url or trap_url:
        msg.add_alternative(body_html(body, pixel_url, trap_url), subtype="html")


def is_loopback_host(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip()).is_loopback
    except ValueError:
        return False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


class MailpitEmailTool:
    # the Gmail subclass (emailtool/gmail.py) sends to Gmail's SMTP instead of a loopback one
    LOOPBACK_ONLY = True
    LABEL = "mailpit"
    # a send plan (task 26) sets each step's date itself: variables plan=1 + due_at_<step>
    SUPPORTS_SCHEDULE = True
    # outreach.sendwindow.SendWindow: working days / hours + time-of-day rotation (set by make_tool
    # for mailbox / gmail mode). None = send the moment a step is due.
    send_window: Any = None

    def __init__(self, webhook_secret: str, *, state_file: Optional[Path] = None,
                 smtp_host: str = DEFAULT_SMTP_HOST, smtp_port: int = DEFAULT_SMTP_PORT,
                 now: Callable[[], datetime] = _utcnow,
                 smtp_factory: Callable[[str, int], Any] = smtplib.SMTP) -> None:
        if self.LOOPBACK_ONLY and not is_loopback_host(smtp_host):
            raise MailpitError("mailpit mode only sends to a loopback SMTP host (127.0.0.1 / localhost)")
        self.webhook_secret = webhook_secret
        self.state_file = Path(state_file) if state_file else DEFAULT_STATE_FILE
        self.smtp_host, self.smtp_port = smtp_host, int(smtp_port)
        self._now = now
        self._smtp_factory = smtp_factory
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.state_file), timeout=10)
        try:
            conn.executescript(_SCHEMA)  # executescript manages its own commit
            for table in ("mp_lead", "mp_send"):  # older state files: the sending mailbox column
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                if "mailbox" not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN mailbox TEXT")
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ state
    @contextmanager
    def _db(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.state_file), timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def _row(self, db: sqlite3.Connection, tool_lead_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM mp_lead WHERE tool_lead_id = ?", (tool_lead_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown tool lead {tool_lead_id}")
        return row

    @staticmethod
    def _remember_msgid(db: sqlite3.Connection, msg_id: str, tool_lead_id: str) -> None:
        """Every Message-ID we send, so a reply can be matched by its In-Reply-To / References even
        when it comes from another address (an alias, an assistant, a forwarding rule)."""
        db.execute("INSERT OR IGNORE INTO mp_msgid (message_id, tool_lead_id) VALUES (?, ?)",
                   (msg_id.strip(), tool_lead_id))

    def lead_email_for_message_ids(self, ids: List[str]) -> Optional[str]:
        ids = [i.strip() for i in ids if i and i.strip()]
        if not ids:
            return None
        with self._db() as db:
            row = db.execute(
                "SELECT l.email FROM mp_msgid m JOIN mp_lead l ON l.tool_lead_id = m.tool_lead_id "
                f"WHERE m.message_id IN ({','.join('?' * len(ids))}) ORDER BY l.rowid DESC LIMIT 1", ids).fetchone()
        return row["email"] if row else None

    @staticmethod
    def _blocked(db: sqlite3.Connection, email: str) -> bool:
        return db.execute("SELECT 1 FROM mp_blocked WHERE email = ?", (email.lower(),)).fetchone() is not None

    def _set_status(self, tool_lead_id: str, status: str, only_from: tuple = ()) -> None:
        with self._db(immediate=True) as db:
            row = self._row(db, tool_lead_id)
            if only_from and row["status"] not in only_from:
                return
            db.execute("UPDATE mp_lead SET status = ? WHERE tool_lead_id = ?", (status, tool_lead_id))
        logger.info("mailpit tool: %s -> %s", tool_lead_id, status)

    # ------------------------------------------------------------------ EmailTool port
    def add_lead(self, campaign_id: str, lead: Any, variables: Mapping[str, str]) -> str:
        email = (getattr(lead, "email", None) or "").strip().lower()
        if not email:
            raise MailpitError("lead has no email")
        tool_lead_id = f"mp-{uuid.uuid4().hex[:12]}"
        with self._db(immediate=True) as db:
            if self._blocked(db, email):
                raise MailpitError("address is blocked")
            db.execute(
                "INSERT INTO mp_lead (tool_lead_id, lead_id, email, campaign_id, variables, enrolled_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (tool_lead_id, getattr(lead, "id", None), email, campaign_id,
                 json.dumps(dict(variables)), _iso(self._now())))
        # Step 1 goes out on the sender's next pass (a few seconds), like a real tool's schedule.
        logger.info("mailpit tool: enrolled lead %s as %s", getattr(lead, "id", None), tool_lead_id)
        return tool_lead_id

    def update_variables(self, tool_lead_id: str, variables: Mapping[str, str]) -> None:
        with self._db(immediate=True) as db:
            row = self._row(db, tool_lead_id)
            merged = {**json.loads(row["variables"]), **dict(variables)}
            db.execute("UPDATE mp_lead SET variables = ? WHERE tool_lead_id = ?",
                       (json.dumps(merged), tool_lead_id))

    def pause(self, tool_lead_id: str) -> None:
        self._set_status(tool_lead_id, "paused", only_from=("active",))

    def resume(self, tool_lead_id: str) -> None:
        self._set_status(tool_lead_id, "active", only_from=("paused",))

    def stop(self, tool_lead_id: str) -> None:
        self._set_status(tool_lead_id, "stopped")

    def block(self, email: str) -> None:
        email = email.strip().lower()
        with self._db(immediate=True) as db:
            db.execute("INSERT OR IGNORE INTO mp_blocked (email) VALUES (?)", (email,))
            db.execute("UPDATE mp_lead SET status = 'stopped' WHERE email = ?", (email,))
        logger.info("mailpit tool: an address was blocked")

    def find_lead(self, email: str) -> Optional[str]:
        with self._db() as db:
            row = db.execute("SELECT tool_lead_id FROM mp_lead WHERE email = ? ORDER BY rowid DESC LIMIT 1",
                             (email.strip().lower(),)).fetchone()
        return row["tool_lead_id"] if row else None

    def reply(self, tool_lead_id: str, body: str, html: Optional[str] = None) -> bool:
        """A one-off reply in the lead's thread (booking link, slots, reschedule, or a template the
        product manager sends from the web app, with its designed `html`): sent now."""
        with self._db(immediate=True) as db:
            row = self._row(db, tool_lead_id)
            if self._blocked(db, row["email"]):
                return False
            subject = thread_subject(row["last_subject"] or "Following up")
            unsub = json.loads(row["variables"]).get(f"unsubscribe_url_{max(row['last_step'], 1)}")
            msg_id = self._smtp_send(row["email"], subject, body, in_reply_to=row["last_message_id"],
                                     unsubscribe_url=unsub or None, mailbox=row["mailbox"], html_body=html)
            db.execute("UPDATE mp_lead SET last_message_id = ? WHERE tool_lead_id = ?", (msg_id, tool_lead_id))
            self._remember_msgid(db, msg_id, tool_lead_id)
        logger.info("%s tool: reply sent for %s", self.LABEL, tool_lead_id)
        return True

    def parse_webhook(self, headers: Mapping[str, str], body: bytes,
                      received_at: Optional[datetime] = None) -> List[NormalizedEvent]:
        return parse_instantly_webhook(headers, body, self.webhook_secret, received_at)

    def mailbox_stats(self, campaign_id: str, since: datetime) -> Dict[str, int]:
        with self._db() as db:
            n = db.execute("SELECT COUNT(*) FROM mp_send WHERE campaign_id = ? AND sent_at >= ?",
                           (campaign_id, _iso(since))).fetchone()[0]
        return {"sent": int(n), "bounced": 0}

    # ------------------------------------------------------------------ sending (used by the sender)
    def _smtp_send(self, to: str, subject: str, body: str, in_reply_to: Optional[str] = None,
                   pixel_url: Optional[str] = None, unsubscribe_url: Optional[str] = None,
                   trap_url: Optional[str] = None, mailbox: Optional[str] = None,
                   html_body: Optional[str] = None) -> str:
        msg = EmailMessage()
        msg["From"] = FROM_ADDR
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = format_datetime(self._now())
        msg_id = make_msgid(domain="ozoo.local")
        msg["Message-ID"] = msg_id
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        set_unsubscribe_headers(msg, unsubscribe_url)
        set_body(msg, body, pixel_url, trap_url, html_body)
        smtp = self._smtp_factory(self.smtp_host, self.smtp_port)
        try:
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001 - the message is already handed over
                pass
        return msg_id

    def _choose_mailbox(self, db: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> Optional[str]:
        """The mailbox this step goes out from ("" = the only one), or None to wait (the mailbox
        tool's daily caps). Mailpit has one sender and no caps."""
        return row["mailbox"] or ""

    def _candidates(self) -> List[str]:
        with self._db() as db:
            return [r["tool_lead_id"] for r in db.execute(
                "SELECT tool_lead_id FROM mp_lead WHERE status = 'active' AND last_step < ? ORDER BY rowid",
                (LAST_STEP,))]

    def due_at(self, enrolled_at: datetime, step: int, schedule_days: Mapping[int, int],
               time_scale: float = 1.0, key: str = "") -> datetime:
        due = enrolled_at + timedelta(days=schedule_days[step]) / time_scale
        if self.send_window is not None and time_scale == 1.0:  # a sped-up local clock skips windows
            return self.send_window.slot(due, step, key)
        return due

    def planned_due(self, variables: Mapping[str, str], step: int, time_scale: float = 1.0,
                    key: str = "") -> Optional[datetime]:
        """A send-plan step's send time: due_at_<step> (set by the agent once it has decided the
        email), moved into the send window unless due_exact_<step> (a date and time the product
        manager picked). None = not decided yet, or on hold: wait."""
        raw = variables.get(f"due_at_{step}") or ""
        try:
            due = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if (self.send_window is not None and time_scale == 1.0
                and variables.get(f"due_exact_{step}") != "1"):
            return self.send_window.slot(due, step, key)
        return due

    def send_due(self, schedule_days: Mapping[int, int], time_scale: float = 1.0) -> List[Dict[str, Any]]:
        """Send every step that is due now. State is re-checked inside the same write transaction
        right before each SMTP send, so a stop / pause / block that landed in the meantime wins.
        Returns the sends made: [{tool_lead_id, lead_id, email, step, subject, sent_at}]."""
        now = self._now()
        candidates = self._candidates()
        sent: List[Dict[str, Any]] = []
        for tid in candidates:
            with self._db(immediate=True) as db:
                row = db.execute("SELECT * FROM mp_lead WHERE tool_lead_id = ?", (tid,)).fetchone()
                if row is None or row["status"] != "active" or row["last_step"] >= LAST_STEP:
                    continue
                if self._blocked(db, row["email"]):
                    db.execute("UPDATE mp_lead SET status = 'stopped' WHERE tool_lead_id = ?", (tid,))
                    continue
                step = row["last_step"] + 1
                variables = json.loads(row["variables"])
                if variables.get("plan") == "1":
                    due = self.planned_due(variables, step, time_scale, key=tid)
                    if due is None or due > now:
                        continue
                elif self.due_at(_parse(row["enrolled_at"]), step, schedule_days, time_scale, key=tid) > now:
                    continue
                subject = variables.get(f"subject_{step}")
                body = variables.get(f"body_{step}")
                if not subject or not body:
                    logger.warning("mailpit tool: %s step %s has no content; skipped", tid, step)
                    continue
                mailbox = self._choose_mailbox(db, row, now)
                if mailbox is None:
                    continue  # its mailbox is at today's cap / within its gap: a later pass sends it
                # follow-ups reply in email 1's thread (one conversation in the lead's inbox, as
                # Instantly does with an empty follow-up subject); email 1 opens the thread
                in_reply_to = None
                if step > 1 and row["last_message_id"] and row["last_subject"]:
                    subject = thread_subject(row["last_subject"])
                    in_reply_to = row["last_message_id"]
                msg_id = self._smtp_send(row["email"], subject, body, in_reply_to=in_reply_to,
                                         pixel_url=variables.get(f"open_pixel_{step}") or None,
                                         unsubscribe_url=variables.get(f"unsubscribe_url_{step}") or None,
                                         trap_url=variables.get(f"trap_url_{step}") or None,
                                         mailbox=mailbox or None,
                                         html_body=variables.get(f"html_{step}") or None)
                status = "done" if step >= LAST_STEP else "active"
                db.execute("UPDATE mp_lead SET last_step = ?, last_subject = ?, last_message_id = ?, status = ?, "
                           "mailbox = ? WHERE tool_lead_id = ?", (step, subject, msg_id, status, mailbox or None, tid))
                db.execute("INSERT INTO mp_send (tool_lead_id, campaign_id, step, sent_at, mailbox) "
                           "VALUES (?, ?, ?, ?, ?)", (tid, row["campaign_id"], step, _iso(now), mailbox or None))
                self._remember_msgid(db, msg_id, tid)
            logger.info("mailpit tool: %s step %s sent", tid, step)
            sent.append({"tool_lead_id": tid, "lead_id": row["lead_id"], "email": row["email"],
                         "step": step, "subject": subject, "sent_at": now})
        return sent

    def unnotified(self) -> List[Dict[str, Any]]:
        """Steps sent but not yet reported to the agent (the sent webhook failed or is pending)."""
        with self._db() as db:
            rows = db.execute("SELECT tool_lead_id, lead_id, email, campaign_id, last_step, notified_step "
                              "FROM mp_lead WHERE last_step > notified_step").fetchall()
        return [dict(r) for r in rows]

    def mark_notified(self, tool_lead_id: str, step: int) -> None:
        with self._db(immediate=True) as db:
            db.execute("UPDATE mp_lead SET notified_step = MAX(notified_step, ?) WHERE tool_lead_id = ?",
                       (step, tool_lead_id))
