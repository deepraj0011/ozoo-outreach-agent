"""Who the simulated leads are, and what their mailboxes do. Plain data, no I/O.

A persona is a real-world lead: which of the 4 emails the person actually sees (most see one or
two, many none), what they then do, and the inbox they read in. The inbox brings its own machines:
Apple Mail loads every image on delivery, Microsoft Defender opens every link seconds after
delivery, a bank's security sandbox may click links minutes later, Outlook hides images from the
person. The agent sees all of it mixed together, exactly as in production.

IPs are documentation / example ranges, except where the bot filter must recognise a real provider
(Microsoft 74.224.0.0/14 and Apple 17.0.0.0/8 are listed in config/scanners.yaml).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Tuple

EVERY = 0  # Act.step: on every email of the sequence


@dataclass(frozen=True)
class Act:
    """One thing that happens `after` an email (`step`, or EVERY) was delivered.

    kind: read | click | visit | reply | bounce | unsubscribe | book | scan
      read         the person opens the email (an image load only if the inbox shows images)
      click        the person clicks the link `tag` (their browser runs our click page script)
      visit        the person reads the page they clicked to for `seconds`, scrolling `scroll` %
      reply        the person (or their auto-reply) answers with `text` ({colleague} = a
                   colleague's address at the same company)
      unsubscribe  the person clicks the unsubscribe link and presses the confirm button
      book         the person books a demo slot
      scan         a machine fetches the email's image and links from `ip`: all of them, or only
                   `tags` ("pixel" = the image), `gap_s` apart; `trap` = it also follows the hidden
                   honeypot link; `js` = it runs our click page script like a browser
    """

    step: int
    after: timedelta
    kind: str
    tag: str = ""
    text: str = ""
    seconds: int = 0
    scroll: int = 0
    ip: str = ""
    user_agent: str = ""
    tags: Tuple[str, ...] = ()
    trap: bool = True
    js: bool = False
    gap_s: int = 2


@dataclass(frozen=True)
class Inbox:
    name: str
    person_ip: str
    open_ip: Optional[str] = None  # where the person's own image load comes from; None = images hidden
    open_ua: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
    machines: Tuple[Act, ...] = ()  # what the provider does on every delivered email


@dataclass(frozen=True)
class Persona:
    key: str
    title: str
    story: str  # what the person really does, in plain words
    inbox: Inbox
    acts: Tuple[Act, ...] = ()
    reads: Tuple[int, ...] = ()  # the emails the person actually saw (ground truth)
    interested: bool = False     # ground truth: a lead sales should hear about
    expect: str = "silent"       # the outcome a correct agent reaches (report.outcome)
    expect_note: str = ""


def m(minutes: float) -> timedelta:
    return timedelta(minutes=minutes)


def h(hours: float) -> timedelta:
    return timedelta(hours=hours)


BROWSER = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"
GOOGLE_PROXY_UA = "Mozilla/5.0 (Windows NT 5.1; rv:11.0) Gecko Firefox/11.0 (via ggpht.com GoogleImageProxy)"

GMAIL = Inbox("Gmail / Google Workspace", person_ip="49.36.12.34", open_ip="66.249.84.10",
              open_ua=GOOGLE_PROXY_UA)
APPLE = Inbox("Apple Mail (Privacy Protection)", person_ip="106.51.20.30", open_ip=None, machines=(
    Act(EVERY, m(0.75), "scan", ip="17.58.10.20", user_agent="Mozilla/5.0", tags=("pixel",), trap=False),))
DEFENDER = Inbox("Outlook / Microsoft 365 + Defender", person_ip="115.112.40.25", open_ip=None, machines=(
    Act(EVERY, m(0.6), "scan", ip="74.225.34.10", js=True),))
DEFENDER_NEW_RANGE = Inbox("Outlook + Defender (unlisted Azure range)", person_ip="115.112.41.80", machines=(
    Act(EVERY, m(0.5), "scan", ip="20.207.73.5", js=True),))
BANK_SANDBOX = Inbox("Bank gateway sandbox (follows every link)", person_ip="125.22.30.40", machines=(
    Act(EVERY, m(4), "scan", ip="203.0.113.40", js=True, gap_s=6),))
SMART_SANDBOX = Inbox("Bank gateway sandbox (skips hidden links, delayed)", person_ip="125.22.31.50", machines=(
    Act(EVERY, m(7), "scan", ip="198.51.100.60", js=True, trap=False,
        tags=("pixel", "booking", "case_study", "product", "pricing", "homepage"), gap_s=25),))

YES = "Sounds interesting. Can we talk this week?"

STORY: Tuple[Persona, ...] = (
    Persona("apple_ghost", "Never reads (Apple Mail)", "Never opens any email. Apple Mail still loads "
            "every image on delivery.", APPLE, expect="silent"),
    Persona("defender_ghost", "Never reads (bank on Microsoft 365)", "Never opens any email. Microsoft "
            "Defender opens the image and every link of each email within a minute.", DEFENDER, expect="silent"),
    Persona("gmail_ghost", "Never reads (Gmail)", "Never opens any email.", GMAIL, expect="silent"),
    Persona("sandbox_ghost", "Never reads (bank sandbox that follows every link)", "Never opens any email. "
            "The bank's gateway opens every link, including the hidden one, 4 minutes after delivery.",
            BANK_SANDBOX, expect="silent"),
    Persona("smart_sandbox_ghost", "Never reads (smart bank sandbox)", "Never opens any email. The gateway "
            "skips hidden links and clicks two real links 7 minutes after delivery, running JavaScript.",
            SMART_SANDBOX, expect="check",
            expect_note="known limit: it looks like a person clicking, so sales get a 'check first' "
                        "alert, never a hot-lead alert"),
    Persona("silent_reader", "Reads everything, does nothing", "Opens all 4 emails, never clicks or replies.",
            GMAIL, acts=tuple(Act(s, h(2 + s), "read") for s in (1, 2, 3, 4)), reads=(1, 2, 3, 4)),
    Persona("curious_clicker", "Clicks once, leaves in 4 seconds", "Opens email 2, clicks pricing, closes the "
            "page after 4 seconds.", GMAIL, reads=(2,), expect="check", acts=(
                Act(2, h(3), "read"), Act(2, h(3) + m(1), "click", tag="pricing"),
                Act(2, h(3) + m(1.1), "visit", tag="pricing", seconds=4, scroll=10))),
    Persona("skimmer", "Misses 2 emails, then reads properly", "Misses emails 1 and 2. Opens email 3, reads the "
            "case study for 40 s; opens email 4, reads the pricing page for 70 s.", GMAIL, reads=(3, 4),
            interested=True, expect="hot", acts=(
                Act(3, h(5), "read"), Act(3, h(5) + m(1), "click", tag="case_study"),
                Act(3, h(5) + m(2), "visit", tag="case_study", seconds=40, scroll=70),
                Act(4, h(20), "read"), Act(4, h(20) + m(1), "click", tag="pricing"),
                Act(4, h(20) + m(2.5), "visit", tag="pricing", seconds=70, scroll=85))),
    Persona("late_replier", "Replies to the last email only", "Never sees emails 1 to 3. Replies to email 4, then "
            "books a demo the next day.", DEFENDER, reads=(4,), interested=True, expect="booked", acts=(
                Act(4, h(26), "reply", text=YES), Act(4, h(50), "book"))),
    Persona("early_yes", "Says yes to email 1", "Reads email 1 three hours later, replies yes, books the next "
            "day.", APPLE, reads=(1,), interested=True, expect="booked", acts=(
                Act(1, h(3), "reply", text=YES), Act(1, h(27), "book"))),
    Persona("not_now", "Not now", "Reads email 2, replies that it is not the right time.", DEFENDER_NEW_RANGE,
            reads=(2,), expect="not_now", acts=(
                Act(2, h(4), "reply", text="Thanks, not now. Maybe next quarter, after our budget cycle."),)),
    Persona("ooo", "Out of office", "An auto-reply to email 1: away until 30 November.", GMAIL, expect="ooo",
            acts=(Act(1, m(2), "reply", text="I am out of office until 2026-11-30 with limited access to email."),)),
    Persona("wrong_person", "Wrong person, names a colleague", "Reads email 1, says a colleague handles this.",
            BANK_SANDBOX, reads=(1,), expect="referred", acts=(
                Act(1, h(6), "reply", text="I'm not the right person for this. Please contact {colleague}, "
                                           "she runs collections."),)),
    Persona("unsubscriber", "Unsubscribes", "Reads email 2 and unsubscribes. (Defender also visits the "
            "unsubscribe link of every email.)", DEFENDER, reads=(2,), expect="unsubscribed", acts=(
                Act(2, h(2), "unsubscribe"),)),
    Persona("bounce", "Mailbox no longer exists", "The address passed verification but the mailbox was closed: "
            "email 1 bounces.", GMAIL, expect="bounced", acts=(Act(1, m(1), "bounce"),)),
)

BY_KEY = {p.key: p for p in STORY}

# A realistic mix for cold email to Indian lenders: most people never read; banks sit behind
# Microsoft 365 and security gateways; a few in a hundred reply. Weights per 100 leads.
MIX: Tuple[Tuple[str, float], ...] = (
    ("defender_ghost", 22), ("gmail_ghost", 14), ("apple_ghost", 12), ("sandbox_ghost", 8),
    ("smart_sandbox_ghost", 4), ("silent_reader", 10), ("curious_clicker", 6), ("skimmer", 4),
    ("not_now", 5), ("ooo", 4), ("wrong_person", 3), ("unsubscriber", 3), ("bounce", 2),
    ("late_replier", 1.5), ("early_yes", 1.5),
)
