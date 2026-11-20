"""EMAIL_MODE=mailpit: local SMTP email tool + sender (no network: SMTP is a recording stub)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from outreach import mailpit_sender
from outreach.emailtool.base import EmailTool
from outreach.emailtool.mailpit import MailpitEmailTool, MailpitError, is_loopback_host
from outreach.sequencer import SCHEDULE_DAYS
from tests.fakes import make_settings

T0 = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
EMAIL = "lead@example.com"


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t


class SMTPStub:
    sent: list = []
    on_send = None

    def __init__(self, host, port):
        self.host, self.port = host, port

    def send_message(self, msg):
        SMTPStub.sent.append(msg)
        if SMTPStub.on_send:
            SMTPStub.on_send(msg)

    def quit(self):
        pass


@pytest.fixture(autouse=True)
def _reset_stub():
    SMTPStub.sent = []
    SMTPStub.on_send = None


def _vars(tag=""):
    v = {}
    for s in (1, 2, 3, 4):
        v[f"subject_{s}"] = f"Subject {s}{tag}"
        v[f"body_{s}"] = f"Body {s}{tag} http://127.0.0.1:8000/r/x"
    return v


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def tool(tmp_path, clock):
    return MailpitEmailTool("secret", state_file=tmp_path / "mp.db", now=clock, smtp_factory=SMTPStub)


def lead(i=1, email=EMAIL):
    return SimpleNamespace(id=i, email=email)


def sends(tool, scale=1.0):
    return tool.send_due(SCHEDULE_DAYS, scale)


# ---------------------------------------------------------------- safety

@pytest.mark.parametrize("host,ok", [("127.0.0.1", True), ("localhost", True), ("::1", True),
                                     ("10.0.0.5", False), ("smtp.gmail.com", False)])
def test_loopback_only(tmp_path, host, ok):
    assert is_loopback_host(host) is ok
    if not ok:
        with pytest.raises(MailpitError):
            MailpitEmailTool("s", state_file=tmp_path / "x.db", smtp_host=host)


def test_implements_email_tool_port(tool):
    assert isinstance(tool, EmailTool)


# ---------------------------------------------------------------- schedule

def test_step1_sent_on_next_pass_not_inside_add_lead(tool):
    tid = tool.add_lead("camp", lead(), _vars())
    assert SMTPStub.sent == []
    out = sends(tool)
    assert [s["step"] for s in out] == [1] and out[0]["tool_lead_id"] == tid
    msg = SMTPStub.sent[0]
    assert msg["To"] == EMAIL and msg["Subject"] == "Subject 1"
    assert "http://127.0.0.1:8000/r/x" in msg.get_content()


def test_steps_follow_schedule(tool, clock):
    tool.add_lead("camp", lead(), _vars())
    sends(tool)
    clock.t = T0 + timedelta(days=4) - timedelta(minutes=1)
    assert sends(tool) == []
    clock.t = T0 + timedelta(days=4)
    assert [s["step"] for s in sends(tool)] == [2]
    clock.t = T0 + timedelta(days=30)
    assert [s["step"] for s in sends(tool)] == [3]  # one step per pass
    assert [s["step"] for s in sends(tool)] == [4]
    assert sends(tool) == []  # sequence done
    # follow-ups reply in email 1's thread
    assert [m["Subject"] for m in SMTPStub.sent] == ["Subject 1"] + ["Re: Subject 1"] * 3
    assert SMTPStub.sent[0]["In-Reply-To"] is None
    for prev, msg in zip(SMTPStub.sent, SMTPStub.sent[1:]):
        assert msg["In-Reply-To"] == prev["Message-ID"] and msg["References"] == prev["Message-ID"]
    assert [m.get_content().split()[1] for m in SMTPStub.sent] == ["1", "2", "3", "4"]  # each step's own body


def test_time_scale(tool, clock):
    tool.add_lead("camp", lead(), _vars())
    sends(tool, 1440)
    clock.t = T0 + timedelta(minutes=4)  # 4 days / 1440 = 4 minutes
    assert [s["step"] for s in sends(tool, 1440)] == [2]


def test_update_variables_changes_next_email(tool, clock):
    tid = tool.add_lead("camp", lead(), _vars())
    sends(tool)
    tool.update_variables(tid, {"subject_2": "Pick a time", "body_2": "slots"})
    clock.t = T0 + timedelta(days=4)
    sends(tool)
    assert SMTPStub.sent[-1].get_content().strip() == "slots"  # the refreshed step 2 body
    assert SMTPStub.sent[-1]["Subject"] == "Re: Subject 1"  # still in email 1's thread


# ---------------------------------------------------------------- stop / pause / block

def test_stop_and_pause_take_effect_at_once(tool, clock):
    a = tool.add_lead("camp", lead(1, "a@example.com"), _vars())
    b = tool.add_lead("camp", lead(2, "b@example.com"), _vars())
    tool.stop(a)
    tool.pause(b)
    assert sends(tool) == []
    tool.resume(b)
    assert [s["tool_lead_id"] for s in sends(tool)] == [b]
    tool.resume(a)  # a stopped lead never resumes
    clock.t = T0 + timedelta(days=4)
    assert [s["tool_lead_id"] for s in sends(tool)] == [b]


def test_state_rechecked_right_before_each_send(tool):
    a = tool.add_lead("camp", lead(1, "a@example.com"), _vars())
    b = tool.add_lead("camp", lead(2, "b@example.com"), _vars())
    listed = tool._candidates

    def candidates_then_agent_stops_b():
        out = listed()
        tool.stop(b)  # the agent's stop lands after the due list was built
        return out

    tool._candidates = candidates_then_agent_stops_b
    out = sends(tool)
    assert [s["tool_lead_id"] for s in out] == [a]
    assert [m["To"] for m in SMTPStub.sent] == ["a@example.com"]


def test_block_stops_and_refuses(tool):
    tid = tool.add_lead("camp", lead(), _vars())
    assert tool.find_lead(EMAIL.upper()) == tid
    tool.block(EMAIL)
    assert sends(tool) == []
    assert tool.reply(tid, "hi") is False
    with pytest.raises(MailpitError):
        tool.add_lead("camp", lead(2), _vars())
    assert tool.find_lead("nobody@example.com") is None


# ---------------------------------------------------------------- reply, stats, webhook

def test_reply_sent_now_in_thread(tool):
    tid = tool.add_lead("camp", lead(), _vars())
    sends(tool)
    first_id = SMTPStub.sent[0]["Message-ID"]
    assert tool.reply(tid, "Book here: http://127.0.0.1:8000/r/b") is True
    msg = SMTPStub.sent[-1]
    assert msg["Subject"] == "Re: Subject 1" and msg["In-Reply-To"] == first_id


def test_mailbox_stats(tool):
    tool.add_lead("camp", lead(1, "a@example.com"), _vars())
    tool.add_lead("other", lead(2, "b@example.com"), _vars())
    sends(tool)
    assert tool.mailbox_stats("camp", T0 - timedelta(days=1)) == {"sent": 1, "bounced": 0}


class AppStub:
    def __init__(self, status=200):
        self.status, self.requests = status, []

    def request(self, method, path, headers=None, content=None):
        self.requests.append((path, headers, content))
        return SimpleNamespace(status_code=self.status)


def test_notify_posts_signed_sent_webhook_the_app_can_parse(tool):
    settings = make_settings()
    tool = MailpitEmailTool(settings.tool_webhook_secret, state_file=tool.state_file, now=tool._now,
                            smtp_factory=SMTPStub)
    tool.add_lead("camp", lead(7), _vars())
    app = AppStub()
    sent = mailpit_sender.run_once(tool, settings, "http://x", client=app)
    assert [s["step"] for s in sent] == [1]
    path, headers, body = app.requests[0]
    events = tool.parse_webhook(headers, body)
    assert path == "/webhooks/email"
    assert [(e.kind, e.step, e.email) for e in events] == [("sent", 1, EMAIL)]
    assert tool.unnotified() == []
    mailpit_sender.run_once(tool, settings, "http://x", client=app)
    assert len(app.requests) == 1  # reported once


def test_notify_retries_when_app_rejects(tool):
    settings = make_settings()
    tool.add_lead("camp", lead(), _vars())
    sends(tool)
    assert mailpit_sender.notify(tool, settings, "http://x", client=AppStub(503)) == 0
    assert len(tool.unnotified()) == 1
    assert mailpit_sender.notify(tool, settings, "http://x", client=AppStub(200)) == 1
    assert tool.unnotified() == []


def test_mailpit_down_does_not_crash(tool):
    class Down:
        def __init__(self, *a):
            raise ConnectionRefusedError()

    tool._smtp_factory = Down
    tool.add_lead("camp", lead(), _vars())
    assert mailpit_sender.run_once(tool, make_settings(), "http://x", client=AppStub()) == []
    tool._smtp_factory = SMTPStub
    assert [s["step"] for s in sends(tool)] == [1]  # nothing was marked sent while down


def test_logs_have_no_addresses_or_content(tool, caplog):
    caplog.set_level(logging.DEBUG)
    tid = tool.add_lead("camp", lead(), _vars())
    sends(tool)
    tool.reply(tid, "secret body text")
    tool.block(EMAIL)
    text = caplog.text
    assert EMAIL not in text and "secret body text" not in text and "Subject 1" not in text


# ---------------------------------------------------------------- settings + app wiring

def test_mailpit_mode_local_only():
    from pydantic import ValidationError

    s = make_settings(app_env="local", email_mode="mailpit", tool_api_key="", time_scale=1440)
    assert s.email_mode == "mailpit" and not s.real_sending  # TIME_SCALE allowed: nobody real gets mail
    with pytest.raises(ValidationError, match="not allowed in production"):
        make_settings(email_mode="mailpit")


@pytest.mark.parametrize("host", ["smtp.gmail.com", "10.0.0.5", "0.0.0.0"])
def test_settings_refuse_non_loopback_smtp(host):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="loopback"):
        make_settings(app_env="local", email_mode="mailpit", smtp_host=host)


def test_default_deps_builds_mailpit_tool(tmp_path):
    from outreach.app import default_deps

    s = make_settings(app_env="local", email_mode="mailpit", mailpit_state_file=str(tmp_path / "mp.db"),
                      fake_clock_file=str(tmp_path / "clock.json"))
    deps = default_deps(s)
    assert isinstance(deps.tool, MailpitEmailTool) and deps.tool.state_file == tmp_path / "mp.db"


# ---------------------------------------------------------------- planned steps (P0)

def test_plan_lead_with_six_steps_sends_all_six(tool, clock):
    past = (T0 - timedelta(days=1)).isoformat()
    tid = tool.add_lead("camp", lead(), {"plan": "1", "steps": "6"})
    for k in range(1, 7):
        tool.update_variables(tid, {f"subject_{k}": f"Subject {k}", f"body_{k}": f"Body {k}",
                                    f"due_at_{k}": past})
        assert [s["step"] for s in sends(tool)] == [k]

    assert len(SMTPStub.sent) == 6
    assert all(m["Subject"].startswith("Re: ") for m in SMTPStub.sent[1:])
    assert sends(tool) == []
    with tool._db() as db:
        assert db.execute("SELECT status FROM mp_lead WHERE tool_lead_id = ?", (tid,)).fetchone()["status"] == "done"


def test_steps_default_still_four(tool, clock):
    tool.add_lead("camp", lead(), _vars())
    clock.t = T0 + timedelta(days=60)
    for _ in range(6):
        sends(tool)

    assert len(SMTPStub.sent) == 4
