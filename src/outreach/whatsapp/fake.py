"""In-memory WhatsApp doubles for tests and the simulated e2e run (no network).

FakeWhatsAppClient has WhatsAppClient's methods; FakeWhatsApp is the real WhatsAppSender
policy (window check, templates, ChatMessage log) on top of it, so it satisfies actions.WaPort.
"""
from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import Settings
from .client import WaSendError
from .sender import WhatsAppSender


class FakeWhatsAppClient:
    def __init__(self) -> None:
        self.calls: List[Tuple[Any, ...]] = []
        self.fail_code: Optional[int] = None  # set to e.g. 131026 to make every send fail
        self._ids = itertools.count(1)

    def _send(self, *call: Any) -> str:
        self.calls.append(call)
        if self.fail_code is not None:
            raise WaSendError(self.fail_code, 400)
        return f"wamid.FAKE{next(self._ids)}"

    def send_template(self, phone: str, name: str, lang: str = "en",
                      variables: Optional[Sequence[str]] = None) -> str:
        return self._send("send_template", phone, name, lang, list(variables or []))

    def send_text(self, phone: str, text: str) -> str:
        return self._send("send_text", phone, text)

    def send_cta_link(self, phone: str, text: str, button_text: str, url: str) -> str:
        return self._send("send_cta_link", phone, text, button_text, url)


class FakeWhatsApp(WhatsAppSender):
    def __init__(self, settings: Settings, templates: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(FakeWhatsAppClient(), settings, templates)

    @property
    def calls(self) -> List[Tuple[Any, ...]]:
        return self.client.calls
