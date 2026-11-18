"""EmailTool protocol and the channel-neutral NormalizedEvent shared by every webhook source."""
from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Protocol, runtime_checkable


class BadSignature(Exception):
    """Webhook authentication failed (missing / wrong secret or signature)."""


class ToolUnsupported(Exception):
    """The tool's API has no (verified) way to do this; a human must do it in the tool UI."""


@dataclass(frozen=True)
class NormalizedEvent:
    """One provider event in our vocabulary. Reused by WhatsApp (Task 8) and Cal.com (Task 11).

    kind: sent|open|click|reply|bounce_hard|bounce_soft|unsubscribe|wa_*|booking_created|...
    lead_id: set only when the source already knows our lead id (signed link, Cal.com metadata).
    """

    provider_event_id: str
    channel: str  # email|whatsapp|web|cal
    kind: str
    occurred_at: datetime
    tool_lead_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    step: Optional[int] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    tag: Optional[str] = None
    lead_id: Optional[int] = None


@runtime_checkable
class EmailTool(Protocol):
    def add_lead(self, campaign_id: str, lead: Any, variables: Mapping[str, str]) -> str: ...

    def update_variables(self, tool_lead_id: str, variables: Mapping[str, str]) -> None: ...

    def pause(self, tool_lead_id: str) -> None: ...

    def resume(self, tool_lead_id: str) -> None: ...

    def stop(self, tool_lead_id: str) -> None: ...

    def block(self, email: str) -> None: ...

    def find_lead(self, email: str) -> Optional[str]: ...

    def reply(self, tool_lead_id: str, body: str, html: Optional[str] = None) -> bool: ...

    def parse_webhook(self, headers: Mapping[str, str], body: bytes,
                      received_at: Optional[datetime] = None) -> List[NormalizedEvent]: ...

    def mailbox_stats(self, campaign_id: str, since: datetime) -> Dict[str, int]: ...


def header(headers: Mapping[str, str], name: str) -> Optional[str]:
    """Case-insensitive header lookup that works for plain dicts and Starlette Headers."""
    want = name.lower()
    for k, v in headers.items():
        if k.lower() == want:
            return v
    return None


def check_shared_secret(headers: Mapping[str, str], name: str, secret: str) -> None:
    got = header(headers, name)
    if not got or not secret or not hmac.compare_digest(got.encode(), secret.encode()):
        raise BadSignature(name)
