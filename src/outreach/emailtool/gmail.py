"""EMAIL_MODE=gmail: real email through one free Gmail mailbox (demo / small pilot, local only).

Sending works like the Mailpit tool (same sequence state file, same sender process), but over
Gmail's SMTP with an app password, so the emails land in real inboxes. Replies are read back over
IMAP: unread messages FROM an enrolled lead's address (and Gmail bounce notices about one) are
recorded once, marked read, and reported to the agent by the sender as the same signed
`reply_received` / `email_bounced` webhooks Instantly would send. Mail from anyone else is never
touched (not read, not marked).

Not for production volume: Gmail limits daily sends and is not a cold-email tool. Logs carry ids
only, never addresses, subjects or bodies.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
import smtplib
import sqlite3
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import format_datetime, formataddr, make_msgid, parseaddr
from html import unescape
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .mailpit import MailpitEmailTool, _iso, _parse, _utcnow

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATE_FILE = REPO_ROOT / ".gmail_tool.db"
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993
TEXT_MAX = 5000
BOUNCE_SENDERS = ("mailer-daemon@", "postmaster@")

_INBOUND_SCHEMA = """
CREATE TABLE IF NOT EXISTS mp_inbound (
    message_id TEXT PRIMARY KEY,
    lead_email TEXT NOT NULL,
    lead_id INTEGER,
    kind TEXT NOT NULL,
    text TEXT,
    received_at TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0
);
"""

_QUOTE_HEADER = re.compile(r"^\s*(On\b.{0,200}\bwrote:|-{2,}\s*Original Message\s*-{2,}|_{8,}|From:\s.+)\s*$",
                           re.IGNORECASE)
_ON_START = re.compile(r"^\s*On\s.+", re.IGNORECASE)


def reply_text(body: str) -> str:
    """The new part of a reply: everything above the quoted original."""
    lines = body.replace("\r\n", "\n").split("\n")
    out: List[str] = []
    for i, line in enumerate(lines):
        if line.lstrip().startswith(">"):
            break
        if _QUOTE_HEADER.match(line):
            break
        # Gmail wraps "On <date> <name> <addr> wrote:" over two lines
        if _ON_START.match(line) and i + 1 < len(lines) and lines[i + 1].rstrip().endswith("wrote:"):
            break
        out.append(line)
    return "\n".join(out).strip()[:TEXT_MAX]


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|blockquote).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", html)
    return unescape(re.sub(r"<[^>]+>", "", html))


def _body_text(msg: Any) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        return ""
    return _html_to_text(content) if part.get_content_type() == "text/html" else content


class GmailEmailTool(MailpitEmailTool):
    LOOPBACK_ONLY = False
    LABEL = "gmail"

    def __init__(self, webhook_secret: str, *, address: str, app_password: str, from_name: str = "Ozoo.ai",
                 state_file: Optional[Path] = None, now: Callable[[], datetime] = _utcnow,
                 smtp_factory: Callable[..., Any] = smtplib.SMTP_SSL,
                 imap_factory: Callable[..., Any] = imaplib.IMAP4_SSL) -> None:
        super().__init__(webhook_secret, state_file=state_file or DEFAULT_STATE_FILE, smtp_host=SMTP_HOST,
                         smtp_port=SMTP_PORT, now=now, smtp_factory=smtp_factory)
        self.address = address.strip()
        self._password = app_password.replace(" ", "")
        self.from_name = from_name
        self._imap_factory = imap_factory
        conn = sqlite3.connect(str(self.state_file), timeout=10)
        try:
            conn.executescript(_INBOUND_SCHEMA)
        finally:
            conn.close()

    # ------------------------------------------------------------------ sending
    def _smtp_send(self, to: str, subject: str, body: str, in_reply_to: Optional[str] = None) -> str:
        msg = EmailMessage()
        msg["From"] = formataddr((self.from_name, self.address))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = format_datetime(_utcnow())  # real mail: real time, never the fake clock
        msg_id = make_msgid(domain=self.address.split("@")[-1] or "gmail.com")
        msg["Message-ID"] = msg_id
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        msg.set_content(body)
        smtp = self._smtp_factory(self.smtp_host, self.smtp_port, timeout=30)
        try:
            smtp.login(self.address, self._password)
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001 - the message is already handed over
                pass
        return msg_id

    # ------------------------------------------------------------------ receiving
    def _leads_by_email(self) -> Dict[str, Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT email, lead_id, enrolled_at FROM mp_lead ORDER BY rowid").fetchall()
        return {r["email"]: {"lead_id": r["lead_id"], "enrolled_at": _parse(r["enrolled_at"])} for r in rows}

    def fetch_inbound(self) -> int:
        """Read unread replies and bounce notices about enrolled leads; record each once and mark it
        read in Gmail. Other mail is left exactly as it is. Returns how many were recorded."""
        leads = self._leads_by_email()
        if not leads:
            return 0
        since = min(v["enrolled_at"] for v in leads.values()) - timedelta(days=1)
        imap = self._imap_factory(IMAP_HOST, IMAP_PORT)
        recorded = 0
        try:
            imap.login(self.address, self._password)
            imap.select("INBOX")
            typ, data = imap.search(None, "UNSEEN", "SINCE", since.strftime("%d-%b-%Y"))
            if typ != "OK" or not data or not data[0]:
                return 0
            for num in data[0].split():
                typ, parts = imap.fetch(num, "(BODY.PEEK[])")  # PEEK: stays unread unless it is ours
                raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
                if typ != "OK" or raw is None:
                    continue
                msg = email.message_from_bytes(raw, policy=default_policy)
                item = self._classify(msg, leads)
                if item is None:
                    continue
                with self._db(immediate=True) as db:
                    cur = db.execute(
                        "INSERT OR IGNORE INTO mp_inbound (message_id, lead_email, lead_id, kind, text, received_at)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (item["message_id"], item["email"], leads[item["email"]]["lead_id"], item["kind"],
                         item["text"], _iso(_utcnow())))
                    recorded += cur.rowcount
                imap.store(num, "+FLAGS", "\\Seen")
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass
        if recorded:
            logger.info("gmail tool: %s inbound message(s) recorded", recorded)
        return recorded

    def _classify(self, msg: Any, leads: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        sender = parseaddr(str(msg.get("From", "")))[1].strip().lower()
        message_id = str(msg.get("Message-ID") or "").strip()
        if not sender or not message_id or sender == self.address.lower():
            return None
        if sender in leads:
            text = reply_text(_body_text(msg))
            return {"email": sender, "kind": "reply", "text": text or "(empty reply)", "message_id": message_id}
        if sender.startswith(BOUNCE_SENDERS):
            body = _body_text(msg).lower()
            for addr in leads:
                if addr in body:
                    return {"email": addr, "kind": "bounce", "text": None, "message_id": message_id}
        return None

    def pending_inbound(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM mp_inbound WHERE notified = 0 ORDER BY rowid").fetchall()]

    def mark_inbound_notified(self, message_id: str) -> None:
        with self._db(immediate=True) as db:
            db.execute("UPDATE mp_inbound SET notified = 1 WHERE message_id = ?", (message_id,))

    def last_step(self, email_addr: str) -> int:
        with self._db() as db:
            row = db.execute("SELECT last_step FROM mp_lead WHERE email = ? ORDER BY rowid DESC LIMIT 1",
                             (email_addr,)).fetchone()
        return int(row["last_step"]) if row else 1
