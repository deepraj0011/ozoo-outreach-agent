"""Shared test doubles: Settings builder, Deps fakes (record calls), app engine."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from outreach.config import Settings
from outreach.db import init_db, make_engine
from outreach.models import Alert as AlertRow

WEBHOOK_SECRET = "tool-webhook-secret-value"
INTAKE_KEY = "intake-key-" + "k" * 20
TEAMS_WEBHOOK_URL = ("https://prod-01.westeurope.logic.azure.com/workflows/abc/triggers/manual/paths/invoke"
                     "?api-version=2016-06-01&sig=TEAMS-SIG-SECRET")
TEAMS_SECRET_BYTES = b"teams-outgoing-secret-32-bytes!!"
TEAMS_OUTGOING_SECRET = base64.b64encode(TEAMS_SECRET_BYTES).decode()
TEAMS_TENANT_ID = "tenant-1111"
TEAMS_CHANNEL_ID = "19:sales-channel@thread.tacv2"
TEAMS_ALLOWED = ("aad-raj", "aad-mina")
LINK_TARGETS = {
    "homepage": "https://ozoo.ai/",
    "booking": "https://cal.com/ozoo/demo",
    "pricing": "https://ozoo.ai/pricing",
    "case_study": "https://ozoo.ai/case",
    "product": "https://ozoo.ai/product",
}


def make_settings(**overrides: Any) -> Settings:
    values = dict(
        database_url="sqlite://",
        link_secret="L" * 40,
        link_base_url="https://go.example.com",
        link_targets=LINK_TARGETS,
        tool_name="instantly",
        tool_api_key="tool-api-key",
        tool_webhook_secret=WEBHOOK_SECRET,
        campaign_ids={"in_a": "c1"},
        default_tz_group="in_a",
        llm_provider="anthropic",
        llm_api_key="llm-key",
        llm_classifier_model="model-classifier",
        llm_chat_model="model-chat",
        zerobounce_api_key="zb-key",
        intake_api_key=INTAKE_KEY,
        cal_webhook_secret="cal-secret",
        cal_booking_url="https://cal.com/ozoo/demo",
        teams_webhook_url=TEAMS_WEBHOOK_URL,
        teams_outgoing_secret=TEAMS_OUTGOING_SECRET,
        teams_tenant_id=TEAMS_TENANT_ID,
        teams_sales_channel_id=TEAMS_CHANNEL_ID,
        teams_allowed_aad_ids=",".join(TEAMS_ALLOWED),
        wa_phone_number_id="123",
        wa_access_token="wa-token",
        wa_app_secret="wa-app-secret",
        wa_verify_token="wa-verify",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


# tests' session factory (bind=engine per call); the app builds its own sessionmaker per engine
TestSession = sessionmaker(autoflush=False, expire_on_commit=False)


def make_app_engine():
    engine = make_engine("sqlite://", poolclass=StaticPool,
                         connect_args={"check_same_thread": False})
    init_db(engine)
    return engine


class Recorder:
    def __init__(self) -> None:
        self.calls: List[Tuple[Any, ...]] = []
        self.fail: set = set()

    def _rec(self, name: str, *args: Any) -> None:
        self.calls.append((name, *args))
        if name in self.fail:
            raise RuntimeError(f"{name} failed")

    def names(self) -> List[str]:
        return [c[0] for c in self.calls]


class FakeWa(Recorder):
    def send_text(self, session, lead, key: str, now: datetime) -> None:
        self._rec("send_text", lead.id, key)

    def send_raw_text(self, session, lead, text: str, now: datetime, author: str = "human") -> None:
        self._rec("send_raw_text", lead.id, text, author)

    def send_template(self, session, lead, name: str, now: datetime) -> None:
        self._rec("send_template", lead.id, name)


class FakeOutreach(Recorder):
    def switch(self, session, lead, reason: str, now: datetime) -> None:
        self._rec("switch", lead.id, reason)

    def refresh_next_step(self, session, lead) -> None:
        self._rec("refresh_next_step", lead.id)

    def send_email_text(self, session, lead, key: str, now: datetime) -> None:
        self._rec("send_email_text", lead.id, key)


class FakeAlerter(Recorder):
    """Mirrors TeamsAlerter's contract: writes the Alert row, records instead of posting."""

    repost_ok = True

    def repost(self, session, row, now) -> bool:
        self._rec("repost", row.kind, row.lead_id)
        return self.repost_ok

    def post_text(self, header: str, lines, now) -> bool:
        self._rec("post_text", header, "\n".join(lines))
        return self.repost_ok

    def send(self, kind: str, text: str, lead, now, session=None) -> None:
        self._rec("send", kind, lead.id if lead is not None else None)
        if session is not None:
            session.add(AlertRow(lead_id=lead.id if lead is not None else None, kind=kind,
                                 text=text, created_at=now))
            session.flush()


def make_deps(tool=None, **kw):
    from outreach.actions import Deps
    from outreach.emailtool.fake import FakeEmailTool

    return Deps(
        tool=tool or FakeEmailTool(webhook_secret=WEBHOOK_SECRET),
        wa=kw.pop("wa", None) or FakeWa(),
        alerter=kw.pop("alerter", None) or FakeAlerter(),
        outreach=kw.pop("outreach", None) or FakeOutreach(),
        **kw,
    )


def fixture_body(name: str, **changes: Any) -> bytes:
    from pathlib import Path

    data = json.loads((Path(__file__).parent / "fixtures" / name).read_text())
    data.update(changes)
    return json.dumps(data).encode()
