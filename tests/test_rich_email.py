"""Task 21: designed HTML follow-ups (image, button, footer), product links, served images,
next email version from verified visits, answers grounded in the email the lead replied to."""
from __future__ import annotations

import copy
import re
from email import message_from_bytes
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from outreach.app import create_app, with_lead_token
from outreach.email_answer import build_system
from outreach.emailtool.mailpit import set_body
from outreach.links import VALID_TAGS, VISIT_TAGS
from outreach.richmail import build_html, image_file, rich_parts, strip_markers, with_tracking
from outreach.rules import SetVariant, decide
from outreach.score import SCORE_POINTS
from outreach.sequencer import load_templates, render_step
from tests.conftest import NOW, make_lead
from tests.fakes import make_app_engine, make_deps, make_settings

ST = make_settings()
VALS = {"booking_link": "https://t/r/book", "homepage_link": "https://t/r/home", "product_link": "https://t/r/prod",
        "voice_link": "https://t/r/voice", "chat_link": "https://t/r/chat", "pricing_link": "https://t/r/price"}
BODY = "Hi Asha,\n\nIntro text & more.\n\n[[image]]\n\n[[button]]\n\nSee it: https://t/r/prod\nUnsubscribe: https://t/r/unsub"
RICH = {"image": "call-analysis", "image_link": "product", "caption": "Click", "button": "Book <now>"}


# ---------------------------------------------------------------- richmail
def test_plain_text_drops_markers_and_keeps_links():
    text = strip_markers(BODY)
    assert "[[" not in text and "\n\n\n" not in text
    assert "See it: https://t/r/prod" in text and "Unsubscribe: https://t/r/unsub" in text


def test_html_has_header_image_button_and_footer():
    doc = build_html(BODY, RICH, VALS, "https://go.ozoo.ai", preheader="the hook")
    assert 'src="https://go.ozoo.ai/img/header.png"' in doc and 'href="https://t/r/home"' in doc
    assert 'src="https://go.ozoo.ai/img/call-analysis.jpg"' in doc
    assert re.search(r'href="https://t/r/prod"[^>]*><img src="https://go.ozoo.ai/img/call-analysis', doc)
    assert 'href="https://t/r/book"' in doc and "Book &lt;now&gt;" in doc  # escaped label
    assert "Intro text &amp; more." in doc and "See it &rarr;" in doc
    assert "Unsubscribe</a>" in doc and "Unsubscribe:" not in doc  # moved to the footer
    assert "Voice AI agents</a>" in doc and "the hook" in doc
    assert "[[" not in doc


def test_no_rich_block_means_no_html():
    assert rich_parts(BODY, None, VALS, "https://x")["html"] == ""


def test_unknown_image_is_left_out():
    doc = build_html(BODY, {**RICH, "image": "../../etc/passwd"}, VALS, "https://x")
    assert "passwd" not in doc and image_file("../../etc/passwd") is None


def test_tracking_goes_before_body_end():
    assert with_tracking("<html><body>x</body></html>", "<img p>") == "<html><body>x<img p></body></html>"
    assert with_tracking("<p>", "") == "<p>"


def test_set_body_sends_text_and_designed_html_with_pixel():
    msg = EmailMessage()
    set_body(msg, "plain", "https://t/o/pix", None, "<html><body>designed</body></html>")
    parsed = message_from_bytes(msg.as_bytes())
    parts = {p.get_content_type(): p.get_payload(decode=True).decode() for p in parsed.walk()
             if not p.is_multipart()}
    assert parts["text/plain"].strip() == "plain"
    assert "designed" in parts["text/html"] and "https://t/o/pix" in parts["text/html"]


def test_set_body_plain_only_without_html_or_tracking():
    msg = EmailMessage()
    set_body(msg, "plain", None, None, None)
    assert msg.get_content_type() == "text/plain"


# ---------------------------------------------------------------- templates
def test_email_1_stays_plain_and_2_to_4_are_designed(session):
    tpl = load_templates()
    for role in ("Head of Collections", "CTO", "Head of Customer Service", "Managing Director"):
        lead = make_lead(session, name="Asha Rao", company="Acme", role=role)
        for variants in ([], ["case_study"], ["product"], ["booking_slots"]):
            lead.variant_flags = {"variants": variants}
            assert "html_1" not in render_step(lead, 1, templates=tpl, settings=ST)
            for step in (2, 3, 4):
                out = render_step(lead, step, templates=tpl, settings=ST)
                doc = out[f"html_{step}"]
                assert "[[" not in out[f"body_{step}"] and "[[" not in doc
                assert not re.search(r"\{[a-z_]+\}", doc)
                assert "/img/header.png" in doc and "Hi Asha," in doc


def test_angle_picks_the_image_and_product_page(session):
    tpl = load_templates()
    coll = render_step(make_lead(session, role="Head of Collections"), 2, templates=tpl, settings=ST)["html_2"]
    qa = render_step(make_lead(session, role="VP Quality"), 2, templates=tpl, settings=ST)["html_2"]
    assert "compliance-checks.jpg" in coll and "qa-scorecard.jpg" in qa


def test_every_template_image_exists():
    tpl = load_templates()
    for a in tpl["angles"].values():
        assert image_file(a["image"])
    for name in ("header", "call-analysis", "lender-handshake"):
        assert image_file(name)


def test_markers_without_rich_block_are_rejected(tmp_path):
    import yaml
    t = copy.deepcopy(load_templates())
    t["steps"][2]["product"].pop("rich")
    p = tmp_path / "t.yaml"
    p.write_text(yaml.safe_dump(t))
    with pytest.raises(ValueError):
        load_templates(str(p))


def test_bad_rich_block_is_rejected(tmp_path):
    import yaml
    for bad in ({"image": "nope"}, {"image_link": "evil"}, {"button": "x" * 500}):
        t = copy.deepcopy(load_templates())
        t["steps"][2]["product"]["rich"] = bad
        p = tmp_path / "t.yaml"
        p.write_text(yaml.safe_dump(t))
        with pytest.raises(ValueError):
            load_templates(str(p))


# ---------------------------------------------------------------- links, tags, rules
def test_new_page_tags_are_known_and_scored():
    assert {"voice", "chat", "trial"} <= VALID_TAGS
    assert {"voice", "chat"} <= VISIT_TAGS and "trial" not in VISIT_TAGS  # trial is AWS, not our site
    assert SCORE_POINTS["trial"] == SCORE_POINTS["pricing"]


def test_microsoft_bookings_link_keeps_its_flags():
    url = "https://outlook.office.com/bookwithme/user/a@b.io/meetingtype/X?anonymous&ep=mcard"
    out = with_lead_token(url, 3, ST)
    assert out.startswith(url + "&metadata%5Blead%5D=")
    again = with_lead_token(out, 3, ST)
    assert again.count("metadata%5Blead%5D") == 1


def test_variant_is_set_once(session):
    from tests.test_visits import lead_snap, visit
    assert SetVariant("product") in decide(lead_snap(), visit("chat"), NOW)
    assert SetVariant("product") not in decide(lead_snap(flags={"variants": ["product"]}), visit("chat"), NOW)


# ---------------------------------------------------------------- images route
@pytest.fixture
def client():
    engine = make_app_engine()
    app = create_app(ST, deps=make_deps(), engine=engine, clock=lambda: NOW)
    yield TestClient(app, raise_server_exceptions=False)
    engine.dispose()


def test_image_route_serves_email_images(client):
    r = client.get("/img/header.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert "max-age" in r.headers["cache-control"]
    assert client.get("/img/call-analysis.jpg").headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize("name", ["nope.png", "..%2Fpyproject.toml", "header.PNG", "x.svg", ".env"])
def test_image_route_refuses_anything_else(client, name):
    assert client.get(f"/img/{name}").status_code == 404


# ---------------------------------------------------------------- answers in context
def test_answer_prompt_includes_the_email_they_replied_to():
    system = build_system("KB", NOW.date(), "We wrote about collections calls.")
    assert "<our_email>" in system and "collections calls" in system
    assert "<our_email>" not in build_system("KB", NOW.date())


def test_classifier_answers_with_the_last_email_as_context(session):
    from outreach.classifier import make_classifier
    from outreach.llm.fake import FakeLLM
    from tests.test_email_copy import _reply
    lead = make_lead(session, active_channel="email", state="engaged", role="Head of Collections",
                     name="Asha Rao", company="Acme")
    lead.step_sent = 2
    llm = FakeLLM({"label": "question", "confidence": 0.95, "reason": "asks", "draft_answer": "d"},
                  {"kind": "answer", "text": "Yes, Hindi is supported."})
    make_classifier(make_settings(llm_classifier_model="c"), llm=llm)(
        session, lead, _reply(session, lead, "Which languages?"), NOW)
    system = llm.calls[1]["system"]
    expected = render_step(lead, 2, settings=ST)["body_2"].split("\n")[2]
    assert "<our_email>" in system and expected[:40] in system
    assert "/r/" not in system.split("<our_email>")[1]  # links stripped
