"""In-memory WhatsApp doubles for tests and the simulated e2e run (no network).

FakeWhatsAppClient has WhatsAppClient's methods; FakeWhatsApp is the real WhatsAppSender
policy (window check, templates, ChatMessage log) on top of it, so it satisfies actions.WaPort.
"""
from __future__ import annotations

import itertools
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import Settings
from .client import WaSendError
from .sender import WhatsAppSender

logger = logging.getLogger(__name__)


class FakeWhatsAppClient:
    """log_sends=True (WHATSAPP_MODE=fake in a local run): each send is also logged as metadata
    only (kind and fake message id; never the phone or the text). The text itself is in the
    chat_message table, which the sender writes as usual."""

    def __init__(self, log_sends: bool = False, id_prefix: str = "wamid.FAKE") -> None:
        self.log_sends = log_sends
        self.id_prefix = id_prefix  # a local run uses a per-process prefix: ids never repeat
        self.calls: List[Tuple[Any, ...]] = []
        self.fail_code: Optional[int] = None  # set to e.g. 131026 to make every send fail
        self._ids = itertools.count(1)

    def _send(self, *call: Any) -> str:
        self.calls.append(call)
        if self.fail_code is not None:
            raise WaSendError(self.fail_code, 400)
        wamid = f"{self.id_prefix}{next(self._ids)}"
        if self.log_sends:
            logger.info("fake WhatsApp %s recorded (%s), nothing sent", call[0], wamid)
        return wamid

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
