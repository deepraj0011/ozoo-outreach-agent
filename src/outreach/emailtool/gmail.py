"""EMAIL_MODE=gmail: real email through one free Gmail mailbox (demo / small pilot).

The mailbox tool (emailtool/mailbox.py) with one Gmail account, an app password and no daily ramp
(Gmail's own limit applies). Same sequence state, same sender process, same IMAP reply reading
(IDLE watcher, automatic-reply marking, thread-header matching). For several mailboxes, daily caps
and warm-up use EMAIL_MODE=mailbox.
"""
from __future__ import annotations

import imaplib
import smtplib
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .mailbox import (  # noqa: F401 - re-exported for callers of the gmail module
    BOUNCE_SENDERS, IMAP_TIMEOUT_S, Mailbox, MailboxEmailTool, is_auto_reply, reply_text,
)
from .mailpit import _utcnow

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATE_FILE = REPO_ROOT / ".gmail_tool.db"
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993
GMAIL_DAILY_LIMIT = 400  # under Gmail's 500 / day for a free account


class GmailEmailTool(MailboxEmailTool):
    LABEL = "gmail"

    def __init__(self, webhook_secret: str, *, address: str, app_password: str, from_name: str = "Ozoo.ai",
                 state_file: Optional[Path] = None, now: Callable[[], datetime] = _utcnow,
                 smtp_factory: Callable[..., Any] = smtplib.SMTP_SSL,
                 imap_factory: Callable[..., Any] = imaplib.IMAP4_SSL) -> None:
        box = Mailbox(address=address.strip(), password=app_password.replace(" ", ""), smtp_host=SMTP_HOST,
                      smtp_port=SMTP_PORT, imap_host=IMAP_HOST, imap_port=IMAP_PORT, from_name=from_name,
                      daily_limit=GMAIL_DAILY_LIMIT, warmup_start=None, min_gap_s=0)
        super().__init__(webhook_secret, mailboxes=[box], state_file=state_file or DEFAULT_STATE_FILE, now=now,
                         smtp_factory=smtp_factory, imap_factory=imap_factory)
        self.address = box.address
