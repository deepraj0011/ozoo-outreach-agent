"""Advice on email copy, from the drip-marketing research (docs/reference/drip-marketing-playbook.md).

Pure and advisory: the Templates screen shows it next to the preview; nothing is ever blocked.
The numbers are rules of thumb from vendor studies (Gong n=28M, Instantly, Hunter n=31M), not law.
"""
from __future__ import annotations

import re
from typing import List

MAX_BODY_WORDS = 120        # replies are best under ~100 words (Gong, Instantly: under 80)
MAX_SUBJECT_WORDS = 7       # 2-6 words do best (Gong, Hunter)
MAX_LINKS = 3               # cold mail with many links looks like marketing to filters
EARLY_STEPS = 2             # a direct meeting ask in emails 1-2 cut replies by 44% (Gong, via Apollo)
FOOTER = re.compile(r"(?im)^.*\b(unsubscribe|prefer not to hear)\b")
LINK = re.compile(r"https?://\S+")
MEETING_ASK = re.compile(r"(?i)\b(book (a|your)|\d+[- ]minute|schedule a (call|demo|meeting)|calendar)\b")
SPAMMY_SUBJECT = re.compile(r"(?i)(!|\bfree\b|\bguarantee|\burgent\b|\bact now\b)")


def _words(text: str) -> int:
    return len(re.findall(r"[^\s>]+", text))


def copy_advice(subject: str, text: str, step: int) -> List[str]:
    """Plain-language advice for one rendered email (subject + plain-text part); [] when it reads well."""
    advice: List[str] = []
    footer = FOOTER.search(text)
    main = text[:footer.start()] if footer else text
    words = _words(LINK.sub("", main))
    if words > MAX_BODY_WORDS:
        advice.append(f"Long: {words} words before the footer. Cold emails get the most replies under "
                      "about 100 words (3-4 short sentences).")
    subject_words = _words(subject)
    if subject_words > MAX_SUBJECT_WORDS:
        advice.append(f"The subject has {subject_words} words; 2-6 plain words do best.")
    if SPAMMY_SUBJECT.search(subject):
        advice.append("The subject has '!' or a salesy word (free, guarantee, urgent); these lower opens "
                      "and trip spam filters.")
    links = len(LINK.findall(text))
    if links > MAX_LINKS:
        advice.append(f"{links} links; keep cold emails to 1-2 links plus the unsubscribe link.")
    if step <= EARLY_STEPS and MEETING_ASK.search(main):
        advice.append(f"Email {step} asks for a meeting. In emails 1-2 a question or an offer (a case study, "
                      "a sample report) gets more replies than a meeting ask; keep the booking ask for later.")
    return advice
