"""Copy advice from the drip-marketing research (docs/reference/drip-marketing-playbook.md)."""
from outreach.copycheck import copy_advice

SHORT = "Hi Ravi,\n\nYour team reviews a few calls a week. We review all of them.\n\nWorth a look?\n\nAsha"


def test_short_focused_email_gets_no_advice():
    assert copy_advice("Call reviews at Fino", SHORT, step=3) == []


def test_long_body_is_flagged_and_footer_is_not_counted():
    body = "Hi Ravi,\n\n" + "word " * 130 + "\n\nUnsubscribe: https://x.example/u/1 " + "extra " * 50
    advice = copy_advice("Call reviews", body, step=3)
    assert len(advice) == 1 and "132 words" in advice[0]


def test_long_subject_and_shouty_subject_are_flagged():
    advice = copy_advice("A really long subject line that keeps going on and on", SHORT, step=3)
    assert any("subject" in a.lower() and "11 words" in a for a in advice)
    assert any("!" in a for a in copy_advice("Free demo!", SHORT, step=3))


def test_many_links_are_flagged():
    body = SHORT + "\n" + " ".join(f"https://ozoo.ai/p{i}" for i in range(4))
    assert any("4 links" in a for a in copy_advice("Call reviews", body, step=3))


def test_meeting_ask_in_the_first_two_emails_is_flagged_but_fine_later():
    body = SHORT + "\nBook a 30-minute demo here."
    assert any("meeting" in a.lower() for a in copy_advice("Call reviews", body, step=1))
    assert copy_advice("Call reviews", body, step=3) == []
