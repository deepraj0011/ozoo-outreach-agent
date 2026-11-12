"""In-memory EmailTool for tests and local runs: records calls, parses webhooks like Instantly."""
from __future__ import annotations

import itertools
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from .base import NormalizedEvent
from .instantly import parse_instantly_webhook


class FakeEmailTool:
    def __init__(self, webhook_secret: str = "fake-secret", reply_supported: bool = True) -> None:
        self.webhook_secret = webhook_secret
        self.reply_supported = reply_supported
        self.calls: List[Tuple[Any, ...]] = []
        self.fail: Set[str] = set()
        self.stats: Dict[str, Dict[str, int]] = {}
        self._ids = itertools.count(1)

    def _rec(self, *call: Any) -> None:
        self.calls.append(call)
        if call[0] in self.fail:
            raise RuntimeError(f"{call[0]} failed")

    def add_lead(self, campaign_id: str, lead: Any, variables: Mapping[str, str]) -> str:
        self._rec("add_lead", campaign_id, getattr(lead, "id", None), dict(variables))
        return f"fake-{next(self._ids)}"

    def update_variables(self, tool_lead_id: str, variables: Mapping[str, str]) -> None:
        self._rec("update_variables", tool_lead_id, dict(variables))

    def pause(self, tool_lead_id: str) -> None:
        self._rec("pause", tool_lead_id)

    def resume(self, tool_lead_id: str) -> None:
        self._rec("resume", tool_lead_id)

    def stop(self, tool_lead_id: str) -> None:
        self._rec("stop", tool_lead_id)

    def reply(self, tool_lead_id: str, body: str) -> bool:
        self._rec("reply", tool_lead_id, body)
        return self.reply_supported

    def parse_webhook(self, headers: Mapping[str, str], body: bytes,
                      received_at: Optional[datetime] = None) -> List[NormalizedEvent]:
        return parse_instantly_webhook(headers, body, self.webhook_secret, received_at)

    def mailbox_stats(self, campaign_id: str, since: datetime) -> Dict[str, int]:
        self._rec("mailbox_stats", campaign_id)
        return dict(self.stats.get(campaign_id, {"sent": 0, "bounced": 0}))
