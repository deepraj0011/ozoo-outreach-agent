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
