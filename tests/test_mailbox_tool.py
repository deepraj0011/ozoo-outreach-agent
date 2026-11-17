"""EMAIL_MODE=mailbox: several SMTP / IMAP mailboxes, per-mailbox daily caps with warm-up, a minimum
gap between sends, a lead pinned to one mailbox, IMAP IDLE reply watchers."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from types import SimpleNamespace

import pytest

from outreach.emailtool.mailbox import InboxWatcher, Mailbox, MailboxEmailTool, idle_wait

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
SCHEDULE = {1: 0, 2: 4, 3: 9, 4: 15}


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.user = None

    def login(self, user, pw):
        self.user = user

    def send_message(self, msg):
        FakeSMTP.sent.append((self.user, msg))

    def quit(self):
        pass


def box(addr, **kw):
    kw.setdefault("min_gap_s", 0)
    return Mailbox(address=addr, password="pw", smtp_host="smtp.test", smtp_port=465, imap_host="imap.test",
                   imap_port=993, **kw)


def make(tmp_path, boxes, clock, imap=None):
    FakeSMTP.sent = []
    return MailboxEmailTool("secret", mailboxes=boxes, state_file=tmp_path / "mb.db", now=lambda: clock[0],
                            smtp_factory=FakeSMTP, imap_factory=imap or (lambda *a, **k: None))


def content():
    return {k: v for i in SCHEDULE for k, v in ((f"subject_{i}", f"S{i}"), (f"body_{i}", f"B{i}"))}


def enrol(tool, n):
    return [tool.add_lead("c", SimpleNamespace(id=i + 1, email=f"lead{i}@example.com"), content()) for i in range(n)]


def senders():
    return [parseaddr(m["From"])[1] for _, m in FakeSMTP.sent]


# ---------------------------------------------------------------- sending: rotation, caps, warm-up, gap

def test_new_leads_spread_over_mailboxes_and_stop_at_the_cap(tmp_path):
    clock = [NOW]
    tool = make(tmp_path, [box("a@send.test", daily_limit=2, warmup_start=None),
                           box("b@send.test", daily_limit=2, warmup_start=None)], clock)
    enrol(tool, 5)
    sent = tool.send_due(SCHEDULE)
    assert len(sent) == 4 and sorted(senders()) == ["a@send.test"] * 2 + ["b@send.test"] * 2
    assert all(user == parseaddr(m["From"])[1] for user, m in FakeSMTP.sent)  # each logs in as itself
    assert tool.send_due(SCHEDULE) == []  # both at today's cap: lead 5 waits
    clock[0] = NOW + timedelta(days=1)
    assert [s["lead_id"] for s in tool.send_due(SCHEDULE)] == [5]


def test_follow_up_and_reply_come_from_the_leads_own_mailbox(tmp_path):
    clock = [NOW]
    tool = make(tmp_path, [box("a@send.test"), box("b@send.test")], clock)
    tids = enrol(tool, 2)
    tool.send_due(SCHEDULE)
    first = {m["To"]: (parseaddr(m["From"])[1], m["Message-ID"]) for _, m in FakeSMTP.sent}
    clock[0] = NOW + timedelta(days=4)
    FakeSMTP.sent = []
    tool.send_due(SCHEDULE)
    for _, m in FakeSMTP.sent:
        frm, msgid = first[m["To"]]
        assert parseaddr(m["From"])[1] == frm and m["In-Reply-To"] == msgid and m["Subject"] == "Re: S1"
    FakeSMTP.sent = []
    assert tool.reply(tids[0], "Here is the link")
    assert parseaddr(FakeSMTP.sent[0][1]["From"])[1] == first["lead0@example.com"][0]


def test_warm_up_ramps_the_daily_cap():
    b = box("a@send.test", daily_limit=30, warmup_start=10, warmup_step=2)
    assert [b.cap_on(d) for d in (0, 1, 5, 10, 30)] == [10, 12, 20, 30, 30]
    assert box("x@send.test", daily_limit=30, warmup_start=None).cap_on(0) == 30


def test_warm_up_counts_days_from_the_mailboxs_first_send(tmp_path):
    clock = [NOW]
    tool = make(tmp_path, [box("a@send.test", daily_limit=30, warmup_start=1, warmup_step=1)], clock)
    enrol(tool, 10)
    assert len(tool.send_due(SCHEDULE)) == 1  # day 0: cap 1
    clock[0] = NOW + timedelta(days=2)
    assert len(tool.send_due(SCHEDULE)) == 3  # day 2: cap 3
    assert tool.mailbox_report() == [{"mailbox": 0, "sent_today": 3, "cap_today": 3}]


def test_minimum_gap_between_two_sends(tmp_path):
    clock = [NOW]
    tool = make(tmp_path, [box("a@send.test", min_gap_s=60)], clock)
    enrol(tool, 3)
    assert len(tool.send_due(SCHEDULE)) == 1
    clock[0] = NOW + timedelta(seconds=30)
    assert tool.send_due(SCHEDULE) == []
    clock[0] = NOW + timedelta(seconds=61)
    assert len(tool.send_due(SCHEDULE)) == 1


def test_pinned_mailbox_at_its_cap_holds_the_follow_up(tmp_path):
    clock = [NOW]
    tool = make(tmp_path, [box("a@send.test", daily_limit=1, warmup_start=None),
                           box("b@send.test", daily_limit=5, warmup_start=None)], clock)
    enrol(tool, 1)
    tool.send_due(SCHEDULE)
    pinned = senders()[0]
    clock[0] = NOW + timedelta(days=4)
    tool.send_due(SCHEDULE)
    assert senders() == [pinned, pinned]  # never switches mailbox mid-thread


def test_starttls_for_port_587(tmp_path, monkeypatch):
    calls = []

    class SMTP:
        def __init__(self, host, port, timeout=None):
            calls.append(("connect", port))

        def starttls(self):
            calls.append(("starttls",))

        def login(self, u, p):
            calls.append(("login",))

        def send_message(self, m):
            calls.append(("send",))

        def quit(self):
            pass

    import outreach.emailtool.mailbox as mb
    monkeypatch.setattr(mb.smtplib, "SMTP", SMTP)
    b = Mailbox(address="a@send.test", password="pw", smtp_host="smtp.test", smtp_port=587,
                imap_host="imap.test", imap_port=993, min_gap_s=0)
    tool = MailboxEmailTool("secret", mailboxes=[b], state_file=tmp_path / "s.db", now=lambda: NOW)
    enrol(tool, 1)
    tool.send_due(SCHEDULE)
    assert calls == [("connect", 587), ("starttls",), ("login",), ("send",)]  # TLS before the password


def test_password_never_in_repr():
    assert "pw" not in repr(box("a@send.test")) and "a@send.test" not in repr(box("a@send.test"))


# ---------------------------------------------------------------- reading: every mailbox, IDLE, watcher

def mail(frm, body, mid):
    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = frm, "Re: S1", mid
    m.set_content(body)
    return m.as_bytes()


class FakeIMAP:
    def __init__(self, messages=None, fail=False, idle=False):
        self.messages, self.fail, self.seen = dict(messages or {}), fail, set()
        self.capabilities = (b"IMAP4REV1", b"IDLE") if idle else (b"IMAP4REV1",)
        self.logins = 0

    def login(self, u, p):
        if self.fail:
            raise OSError("down")
        self.logins += 1

    def select(self, box):
        return "OK", [b"1"]

    def search(self, *a):
        return "OK", [b" ".join(k for k in self.messages if k not in self.seen)]

    def fetch(self, num, what):
        return "OK", [(b"1 (BODY[] {n})", self.messages[num]), b")"]

    def store(self, num, op, flag):
        self.seen.add(num)

    def noop(self):
        return "OK", []

    def logout(self):
        pass


def test_fetch_reads_every_mailbox_and_skips_a_broken_one(tmp_path, caplog):
    imaps = {"imap.a": FakeIMAP(fail=True),
             "imap.b": FakeIMAP({b"1": mail("lead0@example.com", "Yes please", "<r1@x>"),
                                 b"2": mail("b@send.test", "our own copy", "<own@x>")})}
    a = Mailbox(address="a@send.test", password="pw", smtp_host="s", smtp_port=465, imap_host="imap.a", imap_port=993)
    b = Mailbox(address="b@send.test", password="pw", smtp_host="s", smtp_port=465, imap_host="imap.b", imap_port=993)
    tool = MailboxEmailTool("secret", mailboxes=[a, b], state_file=tmp_path / "s.db", now=lambda: NOW,
                            smtp_factory=FakeSMTP, imap_factory=lambda host, port, timeout=None: imaps[host])
    enrol(tool, 1)
    assert tool.fetch_inbound() == 1
    assert [r["text"] for r in tool.pending_inbound()] == ["Yes please"]
    assert imaps["imap.b"].seen == {b"1"}  # mail from our own mailbox is never touched
    assert "mailbox 0: IMAP read failed" in caplog.text and "a@send.test" not in caplog.text


class IdleIMAP:
    """Just enough of imaplib for idle_wait: send / readline / sock."""

    def __init__(self, lines, timeout_after=False):
        self.lines, self.sent, self.timeout_after = list(lines), [], timeout_after
        self.sock = SimpleNamespace(gettimeout=lambda: 30, settimeout=lambda t: None)

    def _new_tag(self):
        return b"A1"

    def send(self, data):
        self.sent.append(data)

    def readline(self):
        if not self.lines:
            if self.timeout_after:
                raise TimeoutError("timed out")
            return b""
        return self.lines.pop(0)


def test_idle_returns_on_new_mail_and_ends_idle():
    imap = IdleIMAP([b"+ idling\r\n", b"* 4 EXISTS\r\n", b"A1 OK IDLE terminated\r\n"])
    assert idle_wait(imap, 5) is True
    assert imap.sent == [b"A1 IDLE\r\n", b"DONE\r\n"]


def test_idle_timeout_means_reconnect():
    assert idle_wait(IdleIMAP([b"+ idling\r\n"], timeout_after=True), 5) is False


def test_watcher_reads_replies_wakes_the_sender_and_survives_errors(tmp_path):
    good = FakeIMAP({b"1": mail("lead0@example.com", "Interested", "<r1@x>")})
    connects = []

    def factory(host, port, timeout=None):
        connects.append(1)
        if len(connects) == 1:
            return FakeIMAP(fail=True)  # first connect fails: back off and retry
        return good

    tool = MailboxEmailTool("secret", mailboxes=[box("a@send.test")], state_file=tmp_path / "s.db",
                            now=lambda: NOW, smtp_factory=FakeSMTP, imap_factory=factory)
    enrol(tool, 1)
    stop, woke = threading.Event(), threading.Event()
    w = InboxWatcher(tool, tool.mailboxes[0], 0, stop, on_new=woke.set, poll_s=0.01)
    try:
        w.stop.wait = lambda t: stop.is_set()  # no real backoff sleep in the test
        w.start()
        assert woke.wait(5)
    finally:
        stop.set()
        w.join(5)
    assert [r["text"] for r in tool.pending_inbound()] == ["Interested"] and len(connects) >= 2


# ---------------------------------------------------------------- settings

def test_settings_presets_and_production():
    from tests.fakes import make_settings
    s = make_settings(app_env="production", email_mode="mailbox", whatsapp_mode="real", llm_mode="real",
                      alerts_mode="real", verify_mode="real", zerobounce_api_key="k", wa_phone_number_id="1",
                      wa_access_token="t", teams_webhook_url="https://x.test/hook", llm_provider="anthropic",
                      llm_api_key="k", llm_classifier_model="m", llm_chat_model="m",
                      link_base_url="https://t.test", link_targets={"booking": "https://cal.com/x"},
                      mailboxes=[{"address": "a@send.test", "password": "p", "provider": "zoho_in"},
                                 {"address": "b@send.test", "password": "p", "provider": "custom",
                                  "smtp_host": "mail.send.test", "smtp_port": 587, "imap_host": "mail.send.test"}])
    a, b = s.mailboxes
    assert (a.smtp_host, a.smtp_port, a.imap_host) == ("smtp.zoho.in", 465, "imap.zoho.in")
    assert (b.smtp_host, b.smtp_port, b.imap_port) == ("mail.send.test", 587, 993)
    assert "p" not in repr(a.password) and s.real_sending


@pytest.mark.parametrize("boxes,match", [
    ([], "mailboxes"),
    ([{"address": "a@x.test", "password": "p", "provider": "custom"}], "smtp_host"),
    ([{"address": "a@x.test", "password": "p"}, {"address": "A@x.test", "password": "q"}], "once"),
])
def test_bad_mailbox_settings(boxes, match):
    from tests.fakes import make_settings
    with pytest.raises(Exception, match=match):
        make_settings(app_env="local", email_mode="mailbox", mailboxes=boxes)
