"""EMAIL_MODE=gmail: SMTP send with login, IMAP reply pickup (only enrolled leads' mail), webhook report."""
from datetime import datetime, timezone
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from outreach.emailtool.gmail import GmailEmailTool, reply_text
from outreach import mailpit_sender

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.logged_in = None

    def login(self, user, pw):
        self.logged_in = (user, pw)

    def send_message(self, msg):
        FakeSMTP.sent.append((self.logged_in, msg))

    def quit(self):
        pass


def _mail(frm, body, mid):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = frm, "agent@gmail.com", "Re: Hi", mid
    m.set_content(body)
    return m.as_bytes()


class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages  # num -> bytes
        self.seen = set()

    def __call__(self, host, port, timeout=None):
        self.timeout = timeout
        return self

    def login(self, u, p):
        pass

    def select(self, box):
        return "OK", [b"1"]

    def search(self, *a):
        return "OK", [b" ".join(k for k in self.messages if k not in self.seen)]

    def fetch(self, num, what):
        return "OK", [(b"1 (BODY[] {n})", self.messages[num]), b")"]

    def store(self, num, op, flag):
        self.seen.add(num)

    def logout(self):
        pass


@pytest.fixture
def tool(tmp_path):
    FakeSMTP.sent = []
    imap = FakeIMAP({})
    t = GmailEmailTool("secret", address="agent@gmail.com", app_password="abcd efgh ijkl mnop",
                       state_file=tmp_path / "g.db", now=lambda: NOW, smtp_factory=FakeSMTP, imap_factory=imap)
    t._fake_imap = imap
    return t


def test_sends_through_gmail_with_login(tool):
    tid = tool.add_lead("c1", SimpleNamespace(id=7, email="Boss@Example.com"),
                        {"subject_1": "Hello", "body_1": "Hi there"})
    sent = tool.send_due({1: 0, 2: 4, 3: 9, 4: 15})
    assert [s["step"] for s in sent] == [1]
    (login, msg), = FakeSMTP.sent
    assert login == ("agent@gmail.com", "abcdefghijklmnop")
    assert msg["To"] == "boss@example.com" and "agent@gmail.com" in msg["From"]
    assert tool.find_lead("boss@example.com") == tid


def test_reads_only_enrolled_leads_replies_and_bounces(tool):
    tool.add_lead("c1", SimpleNamespace(id=7, email="boss@example.com"), {})
    tool._fake_imap.messages = {
        b"1": _mail("Boss <boss@example.com>", "Yes, let's talk.\n\nOn Thu, Nov 13 Ozoo wrote:\n> Hi", "<a@x>"),
        b"2": _mail("Friend <friend@example.com>", "unrelated", "<b@x>"),
        b"3": _mail("Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
                    "Address not found: boss@example.com", "<c@x>"),
    }
    assert tool.fetch_inbound() == 2
    assert tool._fake_imap.seen == {b"1", b"3"}  # the unrelated mail stays unread
    rows = {r["kind"]: r for r in tool.pending_inbound()}
    assert rows["reply"]["text"] == "Yes, let's talk." and rows["reply"]["lead_id"] == 7
    assert "bounce" in rows
    assert tool.fetch_inbound() == 0  # nothing new


def test_inbound_reported_as_signed_webhooks(tool, monkeypatch):
    tool.add_lead("c1", SimpleNamespace(id=7, email="boss@example.com"), {})
    tool._fake_imap.messages = {b"1": _mail("boss@example.com", "Interested!", "<a@x>")}
    tool.fetch_inbound()
    posted = []
    monkeypatch.setattr(mailpit_sender, "_post", lambda req, base, client=None: posted.append(req) or 200)
    settings = SimpleNamespace(tool_webhook_secret="secret", campaign_ids={"default": "c1"},
                               default_tz_group="default")
    monkeypatch.setattr(mailpit_sender.devsim, "_campaign", lambda s, lead: "c1")
    assert mailpit_sender.notify_inbound(tool, settings, "http://x") == 1
    assert b"reply_received" in posted[0].body and b"Interested!" in posted[0].body
    assert tool.pending_inbound() == []


def test_reply_text_strips_quotes():
    assert reply_text("Sure\n\nOn Mon, Nov 10, 2026 at 9:00 AM Ozoo <a@b.com>\nwrote:\n> old") == "Sure"
    assert reply_text("Ok\n-----Original Message-----\nFrom: x") == "Ok"
    assert reply_text("Ok\n> quoted") == "Ok"


def test_imap_connection_has_a_timeout(tool):
    """Regression: without one, a stalled Gmail IMAP socket froze the sender (no sends, no replies)."""
    tool.add_lead("c1", SimpleNamespace(id=7, email="boss@example.com"), {"subject_1": "Hello", "body_1": "Hi"})
    tool.fetch_inbound()
    assert tool._fake_imap.timeout and tool._fake_imap.timeout <= 60


def _raw(frm, body, mid, **headers):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = frm, "agent@gmail.com", "Re: Hi", mid
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    return m.as_bytes()


@pytest.mark.parametrize("headers", [{"Auto_Submitted": "auto-replied"}, {"X_Autoreply": "yes"},
                                     {"Precedence": "auto_reply"}])
def test_automatic_replies_are_marked(tool, headers):
    from outreach.classifier import AUTO_REPLY_MARK
    tool.add_lead("c1", SimpleNamespace(id=7, email="boss@example.com"), {})
    tool._fake_imap.messages = {b"1": _raw("boss@example.com", "I am out of office", "<a@x>", **headers)}
    assert tool.fetch_inbound() == 1
    assert tool.pending_inbound()[0]["text"].startswith(AUTO_REPLY_MARK)


def test_auto_submitted_no_is_a_person(tool):
    from outreach.classifier import AUTO_REPLY_MARK
    tool.add_lead("c1", SimpleNamespace(id=7, email="boss@example.com"), {})
    tool._fake_imap.messages = {b"1": _raw("boss@example.com", "Let's talk", "<a@x>", Auto_Submitted="no")}
    tool.fetch_inbound()
    assert not tool.pending_inbound()[0]["text"].startswith(AUTO_REPLY_MARK)


def test_reply_from_another_address_is_matched_by_thread_headers(tool):
    tid = tool.add_lead("c1", SimpleNamespace(id=7, email="boss@example.com"), {"subject_1": "Hello", "body_1": "Hi"})
    tool.send_due({1: 0, 2: 4, 3: 9, 4: 15})
    sent_id = FakeSMTP.sent[-1][1]["Message-ID"]
    tool._fake_imap.messages = {
        b"1": _raw("Assistant <pa@example.com>", "He'd like a call", "<r@x>", In_Reply_To=sent_id),
        b"2": _raw("Stranger <s@other.com>", "hi", "<s@x>", In_Reply_To="<unknown@x>"),
    }
    assert tool.fetch_inbound() == 1
    (row,) = tool.pending_inbound()
    assert row["lead_email"] == "boss@example.com" and row["lead_id"] == 7 and tid
    assert tool._fake_imap.seen == {b"1"}
