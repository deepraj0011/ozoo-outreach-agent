"""Local demo console: lead file import (CSV / Excel) and the whitelisted demo actions."""
import io

import openpyxl
import pytest

from outreach.demo_console import build_argv, parse_leads


def test_csv_with_other_header_names_and_semicolons():
    data = b"Full Name;Company;Job Title;Email;WhatsApp;Opt-in\nA B;Acme;CTO;a@acme.example.com;;\nC D;Beta;;;919800000001;yes\n"
    r = parse_leads("leads.csv", data)
    assert r["columns"]["role"] == "Job Title" and r["columns"]["phone"] == "WhatsApp"
    a, c = (x["payload"] for x in r["leads"])
    assert a["email"] == "a@acme.example.com" and a["consent_whatsapp"] is False
    assert c["phone"] == "+919800000001" and c["consent_whatsapp"] is True and c["role"] == "Not given"


def test_excel_first_last_name_and_numeric_phone():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["First Name", "Last Name", "Company", "Mobile", "Consent"])
    ws.append(["Raj", "K", "Gamma", 919800000002, "Y"])
    buf = io.BytesIO()
    wb.save(buf)
    (row,) = parse_leads("x.xlsx", buf.getvalue())["leads"]
    assert row["payload"]["name"] == "Raj K" and row["payload"]["phone"] == "+919800000002" and not row["problems"]


def test_rows_with_missing_data_are_flagged_not_dropped():
    r = parse_leads("x.csv", b"Name,Company,Email,Phone\nNo Contact,X,,\nPhone Only,Y,,+12015550100\n")
    assert r["leads"][0]["problems"] == ["no email or phone"]
    assert r["leads"][1]["problems"] == ["phone only, but no WhatsApp opt-in"]


@pytest.mark.parametrize("name,data,msg", [
    ("x.pdf", b"x", "upload a .csv or .xlsx"),
    ("x.xls", b"x", "old .xls"),
    ("x.csv", b"Company,Email\nA,a@b.co\n", "no name column"),
    ("x.csv", b"Name,Company\nA,B\n", "no Email or Phone"),
    ("x.csv", b"", "empty"),
])
def test_bad_files_get_a_readable_error(name, data, msg):
    with pytest.raises(ValueError, match=msg):
        parse_leads(name, data)


def test_actions_are_whitelisted():
    assert build_argv({"action": "wa_text", "lead": 2, "text": "hi"}) == ["sim", "wa-text", "--lead", "2", "--text", "hi"]
    assert build_argv({"action": "teams_wa", "lead": 2, "text": "hello"}) == ["sim", "teams", "--text", "wa 2 hello"]
    for bad in ({"action": "rm"}, {"action": "click", "lead": 1, "tag": "unsubscribe; rm"},
                {"action": "wa_text", "lead": "1", "text": "x"}, {"action": "advance", "hours": -1},
                {"action": "email_reply", "lead": 1}):
        with pytest.raises(ValueError):
            build_argv(bad)


def test_console_answers_only_on_this_computer():
    """Through a tunnel the console must not be reachable; tracked links still work."""
    from fastapi.testclient import TestClient
    from outreach.app import create_app
    from outreach.botfilter import Scanners
    from tests.conftest import NOW
    from tests.fakes import make_app_engine, make_deps, make_settings

    settings = make_settings(app_env="local")
    app = create_app(settings, deps=make_deps(), engine=make_app_engine(), clock=lambda: NOW,
                     scanners=Scanners(ua_substrings=(), cidrs=(), apple_proxy_cidrs=()))
    client = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    assert client.get("/demo/api/state").status_code == 200
    for header in ("CF-Connecting-IP", "X-Forwarded-For", "CF-Ray"):
        for path in ("/", "/demo", "/demo/api/state"):
            assert client.get(path, headers={header: "198.51.100.9"}).status_code == 404
        assert client.post("/demo/api/run", json={"action": "tick"},
                           headers={header: "198.51.100.9"}).status_code == 404
    assert client.get("/r/not-a-token", headers={"CF-Connecting-IP": "198.51.100.9"}).status_code == 302


# ---------------------------------------------------------------- WhatsApp phone preview
from types import SimpleNamespace

from outreach.demo_console import chat_view, wa_preview
from outreach.whatsapp.sender import load_wa_templates


def _lead(**kw):
    d = dict(id=7, name="Priya Shah", company="Northwind Finance", phone="+919876543210",
             consent_whatsapp=True, active_channel="whatsapp", state="in_sequence", wa_template_sent_at=None)
    d.update(kw)
    return SimpleNamespace(**d)


def _msg(text, direction="out", author="bot"):
    return SimpleNamespace(id=1, direction=direction, author=author, text=text, created_at=None)


def test_template_message_shows_its_text_and_tappable_buttons():
    t = load_wa_templates()
    (m,) = chat_view(_lead(), [_msg("[template:ozoo_intro]")], t)
    v = m["view"]
    assert v["kind"] == "template" and "Priya" in v["text"] and "Northwind Finance" in v["text"]
    assert "{{" not in v["text"]
    assert [b["payload"] for b in v["buttons"]] == ["BOOK_DEMO", "ASK_QUESTION"]
    assert [b["label"] for b in v["buttons"]] == ["Book a demo", "Ask a question"]


def test_booking_link_text_shows_its_link_button():
    t = load_wa_templates()
    text = t["texts"]["booking_link"].format(first_name="Priya", company="Northwind Finance", booking_link="")
    (m,) = chat_view(_lead(), [_msg(text)], t)
    assert m["view"] == {"kind": "cta", "text": text, "cta": t["buttons"]["booking_link"]}


def test_plain_text_and_lead_messages_pass_through():
    (m,) = chat_view(_lead(), [_msg("Hello there", "in", "lead")], load_wa_templates())
    assert m["view"] == {"kind": "text", "text": "Hello there"}


def test_preview_before_anything_is_sent():
    t = load_wa_templates()
    p = wa_preview(_lead(), t)
    assert p["will_send"] is True and p["message"]["kind"] == "template" and "Priya" in p["message"]["text"]
    assert wa_preview(_lead(consent_whatsapp=False), t)["will_send"] is False
    assert wa_preview(_lead(active_channel="email"), t)["will_send"] is False
    assert wa_preview(_lead(phone=None), t) is None
