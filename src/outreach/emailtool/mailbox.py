"""EMAIL_MODE=mailbox: our own sender over ordinary mailboxes (Google Workspace, Zoho, Microsoft 365
or any SMTP + IMAP account). The free, open alternative to a cold-email SaaS.

- Several mailboxes: each lead is pinned to one (follow-ups and replies stay in its thread); new
  leads go to the mailbox with the most room left today.
- Per-mailbox daily cap with a warm-up ramp (start low, add a few a day) and a minimum gap between
  two sends, so a new mailbox / domain builds reputation slowly.
- Replies and bounces: one IMAP IDLE watcher per mailbox (seconds, no polling); a stalled
  connection times out and reconnects, so it can never freeze the sender. Automatic replies (RFC
  3834 headers) are marked and never count as a person engaging; a reply from another address in
  our thread is matched by In-Reply-To / References.

EMAIL_MODE=gmail (emailtool/gmail.py) is this tool with one free Gmail mailbox and no caps.
Logs carry ids only, never addresses, passwords, subjects or bodies.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
import smtplib
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import format_datetime, formataddr, make_msgid, parseaddr
from html import unescape
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..classifier import AUTO_REPLY_MARK
from .mailpit import MailpitEmailTool, _iso, _parse, _utcnow, set_body, set_unsubscribe_headers

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATE_FILE = REPO_ROOT / ".mailbox_tool.db"
TEXT_MAX = 5000
BOUNCE_SENDERS = ("mailer-daemon@", "postmaster@")
IMAP_TIMEOUT_S = 30  # every IMAP command; IDLE waits use IDLE_S
SMTP_TIMEOUT_S = 30
IDLE_S = 9 * 60  # under the 29 minutes RFC 2177 allows; then reconnect and re-check
POLL_S = 30  # servers without IDLE
BACKOFF_MAX_S = 300


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


@dataclass(frozen=True)
class Mailbox:
    address: str
    password: str
    smtp_host: str
    smtp_port: int
    imap_host: str
    imap_port: int
    from_name: str = "Ozoo.ai"
    daily_limit: int = 30
    warmup_start: Optional[int] = 10  # None: the full daily_limit from day one
    warmup_step: int = 2  # added to the cap per day since this mailbox's first send
    min_gap_s: float = 60.0

    def __repr__(self) -> str:  # never the password (or the address) in a log / traceback
        return f"Mailbox(smtp={self.smtp_host}:{self.smtp_port}, limit={self.daily_limit})"

    def cap_on(self, days_since_first_send: int) -> int:
        if not self.warmup_start:
            return self.daily_limit
        return min(self.daily_limit, self.warmup_start + self.warmup_step * max(days_since_first_send, 0))


# ---------------------------------------------------------------- reading replies

_QUOTE_HEADER = re.compile(r"^\s*(On\b.{0,200}\bwrote:|-{2,}\s*Original Message\s*-{2,}|_{8,}|From:\s.+)\s*$",
                           re.IGNORECASE)
_ON_START = re.compile(r"^\s*On\s.+", re.IGNORECASE)
_MSGID = re.compile(r"<[^<>\s]+>")
_AUTO_PRECEDENCE = ("auto_reply", "bulk", "junk")


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


def is_auto_reply(msg: Any) -> bool:
    """RFC 3834 and the common vendor headers: true when a machine wrote this message
    (out-of-office, vacation, ticket confirmations). Such a reply never counts as engagement."""
    auto = str(msg.get("Auto-Submitted", "") or "").strip().lower()
    if auto and auto != "no":
        return True
    if msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    return str(msg.get("Precedence", "") or "").strip().lower() in _AUTO_PRECEDENCE


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


# ---------------------------------------------------------------- IMAP IDLE (RFC 2177)

def idle_wait(imap: Any, timeout_s: float) -> bool:
    """Block in IDLE until the server reports new mail (True) or timeout_s passes (False). After a
    False the connection is no longer usable (the socket timed out mid-read): reconnect."""
    tag = imap._new_tag()
    imap.send(tag + b" IDLE\r\n")
    first = imap.readline()
    if not first.startswith(b"+"):
        raise imaplib.IMAP4.error("IDLE refused")
    sock = imap.sock
    old = sock.gettimeout()
    sock.settimeout(timeout_s)
    try:
        while True:
            line = imap.readline()
            if not line:
                raise imaplib.IMAP4.abort("connection closed during IDLE")
            if line.startswith(b"*") and (b"EXISTS" in line or b"RECENT" in line):
                break
    except (TimeoutError, OSError) as exc:
        if isinstance(exc, TimeoutError) or "timed out" in str(exc):
            return False
        raise
    sock.settimeout(old)
    imap.send(b"DONE\r\n")
    while True:
        line = imap.readline()
        if not line:
            raise imaplib.IMAP4.abort("connection closed after IDLE")
        if line.startswith(tag):
            return True


class InboxWatcher(threading.Thread):
    """One mailbox: connect, read what is unread, then IDLE until new mail (or reconnect every
    IDLE_S); any error waits (5 s doubling to 5 min) and reconnects. Never raises."""

    def __init__(self, tool: "MailboxEmailTool", box: Mailbox, index: int, stop: threading.Event,
                 on_new: Optional[Callable[[], None]] = None, idle_s: float = IDLE_S, poll_s: float = POLL_S) -> None:
        super().__init__(name=f"inbox-watcher-{index}", daemon=True)
        self.tool, self.box, self.index, self.stop = tool, box, index, stop
        self.on_new, self.idle_s, self.poll_s = on_new, idle_s, poll_s

    def run(self) -> None:
        backoff = 5.0
        while not self.stop.is_set():
            imap = None
            try:
                imap = self.tool._connect(self.box)
                backoff = 5.0
                idle_ok = b"IDLE" in tuple(getattr(imap, "capabilities", ()) or ())
                while not self.stop.is_set():
                    if self.tool._fetch_on(imap, self.box) and self.on_new:
                        self.on_new()
                    if idle_ok:
                        if not idle_wait(imap, self.idle_s):
                            break  # quiet for idle_s: reconnect (a fresh session, a fresh check)
                    else:
                        self.stop.wait(self.poll_s)
                        imap.noop()
            except Exception as exc:  # noqa: BLE001 - the watcher must outlive any server trouble
                logger.warning("mailbox %s: IMAP watcher error (%s); reconnecting in %ss", self.index,
                               type(exc).__name__, int(backoff))
                self.stop.wait(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_S)
            finally:
                _close(imap)


def _close(imap: Any) -> None:
    if imap is None:
        return
    try:
        imap.logout()
    except Exception:  # noqa: BLE001
        try:
            imap.shutdown()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- the tool

class MailboxEmailTool(MailpitEmailTool):
    LOOPBACK_ONLY = False
    LABEL = "mailbox"

    def __init__(self, webhook_secret: str, *, mailboxes: Sequence[Mailbox], state_file: Optional[Path] = None,
                 now: Callable[[], datetime] = _utcnow, smtp_factory: Optional[Callable[..., Any]] = None,
                 imap_factory: Callable[..., Any] = imaplib.IMAP4_SSL) -> None:
        if not mailboxes:
            raise ValueError("mailbox mode needs at least one mailbox")
        boxes = {b.address.strip().lower(): b for b in mailboxes}
        if len(boxes) != len(mailboxes):
            raise ValueError("each mailbox address may be listed once")
        first = mailboxes[0]
        super().__init__(webhook_secret, state_file=state_file or DEFAULT_STATE_FILE, smtp_host=first.smtp_host,
                         smtp_port=first.smtp_port, now=now, smtp_factory=smtp_factory or smtplib.SMTP_SSL)
        self._custom_smtp = smtp_factory is not None
        self._boxes = boxes
        self._order = [b.address.strip().lower() for b in mailboxes]
        self._imap_factory = imap_factory
        conn = sqlite3.connect(str(self.state_file), timeout=10)
        try:
            conn.executescript(_INBOUND_SCHEMA)
        finally:
            conn.close()

    @property
    def mailboxes(self) -> List[Mailbox]:
        return [self._boxes[a] for a in self._order]

    # ------------------------------------------------------------------ which mailbox sends
    @staticmethod
    def _day_start(now: datetime) -> datetime:
        u = now.astimezone(timezone.utc)
        return u.replace(hour=0, minute=0, second=0, microsecond=0)

    def cap_today(self, db: sqlite3.Connection, addr: str, now: datetime) -> int:
        box = self._boxes[addr]
        first = db.execute("SELECT MIN(sent_at) FROM mp_send WHERE mailbox = ?", (addr,)).fetchone()[0]
        days = (self._day_start(now) - self._day_start(_parse(first))).days if first else 0
        return box.cap_on(days)

    def _room(self, db: sqlite3.Connection, addr: str, now: datetime) -> Optional[int]:
        """Sends left today for this mailbox, or None when it must wait (cap or gap)."""
        box = self._boxes[addr]
        sent_today = db.execute("SELECT COUNT(*) FROM mp_send WHERE mailbox = ? AND sent_at >= ?",
                                (addr, _iso(self._day_start(now)))).fetchone()[0]
        left = self.cap_today(db, addr, now) - int(sent_today)
        if left <= 0:
            return None
        if box.min_gap_s > 0:
            last = db.execute("SELECT MAX(sent_at) FROM mp_send WHERE mailbox = ?", (addr,)).fetchone()[0]
            if last and (now - _parse(last)).total_seconds() < box.min_gap_s:
                return None
        return left

    def _choose_mailbox(self, db: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> Optional[str]:
        pinned = (row["mailbox"] or "").lower()
        if pinned in self._boxes:  # the lead's thread lives in this mailbox: wait for it
            return pinned if self._room(db, pinned, now) is not None else None
        best, best_left = None, 0
        for addr in self._order:
            left = self._room(db, addr, now)
            if left is not None and left > best_left:
                best, best_left = addr, left
        return best

    # ------------------------------------------------------------------ SMTP
    def _smtp_send(self, to: str, subject: str, body: str, in_reply_to: Optional[str] = None,
                   pixel_url: Optional[str] = None, unsubscribe_url: Optional[str] = None,
                   trap_url: Optional[str] = None, mailbox: Optional[str] = None,
                   html_body: Optional[str] = None) -> str:
        box = self._boxes.get((mailbox or "").lower()) or self._boxes[self._order[0]]
        msg = EmailMessage()
        msg["From"] = formataddr((box.from_name, box.address))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = format_datetime(_utcnow())  # real mail: real time, never the fake clock
        msg_id = make_msgid(domain=box.address.split("@")[-1] or "localhost")
        msg["Message-ID"] = msg_id
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        set_unsubscribe_headers(msg, unsubscribe_url)
        set_body(msg, body, pixel_url, trap_url, html_body)
        if self._custom_smtp or box.smtp_port == 465:
            smtp = self._smtp_factory(box.smtp_host, box.smtp_port, timeout=SMTP_TIMEOUT_S)
        else:  # 587 / 25: plain connect, then STARTTLS before the password goes over the wire
            smtp = smtplib.SMTP(box.smtp_host, box.smtp_port, timeout=SMTP_TIMEOUT_S)
            smtp.starttls()
        try:
            smtp.login(box.address, box.password)
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001 - the message is already handed over
                pass
        return msg_id

    # ------------------------------------------------------------------ IMAP
    def _connect(self, box: Mailbox) -> Any:
        imap = self._imap_factory(box.imap_host, box.imap_port, timeout=IMAP_TIMEOUT_S)
        try:
            imap.login(box.address, box.password)
            imap.select("INBOX")
        except Exception:
            _close(imap)
            raise
        return imap

    def _leads_by_email(self) -> Dict[str, Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT email, lead_id, enrolled_at FROM mp_lead ORDER BY rowid").fetchall()
        return {r["email"]: {"lead_id": r["lead_id"], "enrolled_at": _parse(r["enrolled_at"])} for r in rows}

    def _fetch_on(self, imap: Any, box: Mailbox) -> int:
        """Record unread replies / bounce notices about enrolled leads on an open, selected
        connection; mark each one read. Other mail is left exactly as it is."""
        leads = self._leads_by_email()
        if not leads:
            return 0
        since = min(v["enrolled_at"] for v in leads.values()) - timedelta(days=1)
        typ, data = imap.search(None, "UNSEEN", "SINCE", since.strftime("%d-%b-%Y"))
        if typ != "OK" or not data or not data[0]:
            return 0
        recorded = 0
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
        if recorded:
            logger.info("%s tool: %s inbound message(s) recorded", self.LABEL, recorded)
        return recorded

    def fetch_inbound(self) -> int:
        """One pass over every mailbox (the sender's --once mode and tests; the running sender uses
        the IDLE watchers). A mailbox that fails is logged and skipped. Returns how many recorded."""
        total = 0
        for i, box in enumerate(self.mailboxes):
            imap = None
            try:
                imap = self._connect(box)
                total += self._fetch_on(imap, box)
            except (OSError, imaplib.IMAP4.error) as exc:
                logger.warning("mailbox %s: IMAP read failed (%s); is IMAP enabled for it?", i, type(exc).__name__)
            finally:
                _close(imap)
        return total

    def start_watchers(self, stop: threading.Event, on_new: Optional[Callable[[], None]] = None,
                       **kw: Any) -> List[InboxWatcher]:
        watchers = [InboxWatcher(self, box, i, stop, on_new, **kw) for i, box in enumerate(self.mailboxes)]
        for w in watchers:
            w.start()
        return watchers

    def _classify(self, msg: Any, leads: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        sender = parseaddr(str(msg.get("From", "")))[1].strip().lower()
        message_id = str(msg.get("Message-ID") or "").strip()
        if not sender or not message_id or sender in self._boxes:
            return None
        if sender.startswith(BOUNCE_SENDERS):
            body = _body_text(msg).lower()
            for addr in leads:
                if addr in body:
                    return {"email": addr, "kind": "bounce", "text": None, "message_id": message_id}
            return None
        lead_email = sender if sender in leads else None
        if lead_email is None:  # another address answering in our thread (alias, assistant)
            refs = _MSGID.findall(f"{msg.get('In-Reply-To', '')} {msg.get('References', '')}")
            found = self.lead_email_for_message_ids(refs)
            lead_email = found if found in leads else None
        if lead_email is None:
            return None
        text = reply_text(_body_text(msg)) or "(empty reply)"
        if is_auto_reply(msg):
            text = f"{AUTO_REPLY_MARK} {text}"
        return {"email": lead_email, "kind": "reply", "text": text[:TEXT_MAX], "message_id": message_id}

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

    def mailbox_report(self) -> List[Dict[str, Any]]:
        """Per mailbox (by position, no addresses): sent today and today's cap."""
        now = self._now()
        out = []
        with self._db() as db:
            for i, addr in enumerate(self._order):
                sent = db.execute("SELECT COUNT(*) FROM mp_send WHERE mailbox = ? AND sent_at >= ?",
                                  (addr, _iso(self._day_start(now)))).fetchone()[0]
                out.append({"mailbox": i, "sent_today": int(sent), "cap_today": self.cap_today(db, addr, now)})
        return out
