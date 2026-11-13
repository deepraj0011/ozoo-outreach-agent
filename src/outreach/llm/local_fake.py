"""RuleBasedLLM: a deterministic, offline stand-in for the LLM port (LLM_MODE=fake, local runs only).

It answers the same two forced tool calls as a real provider (classify_reply, bot_reply) with the
same dict shapes, so the classifier's and chatbot's validation, guardrails and post-checks all
still run on its output. Pure keyword rules: no network, no cost, same answer every time.

  classify_reply  unsubscribe > ooo (+ return date) > wrong_person (+ email in the text) >
                  not_now (+ date) > not_interested > interested > question (? or question word) >
                  unsure
  bot_reply       "human" / person -> wants_human; any price word -> wants_human; not ready ->
                  not_ready; book / demo / call -> ready_to_book; else the KB line sharing the most
                  words with the message -> answer (that line, word for word); none -> out_of_scope

Never logs message text.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .base import LLMBadOutput

CONFIDENCE = 0.9
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december")

_UNSUB = re.compile(r"\b(unsubscribe|remove me|opt[ -]?out|stop (emailing|contacting|messaging)( me)?|"
                    r"take me off|do not (contact|email)|don'?t (contact|email))\b")
_OOO = re.compile(r"\b(out of (the )?office|ooo|on (vacation|holiday|leave|parental leave)|away until|"
                  r"auto(matic)?[- ]?reply)\b")
_WRONG = re.compile(r"\b(wrong (person|contact)|not the right (person|contact)|no longer (work|with)|"
                    r"not responsible for|right person (is|would be)|you should (contact|talk to|reach))\b")
_NOT_NOW = re.compile(r"\b(not now|not right now|later|next (week|month|quarter|year)|in a few (weeks|months)|"
                      r"circle back|try (again )?in|reach out in|maybe in)\b")
_NOT_INTERESTED = re.compile(r"\b(not interested|no thanks|no,? thank you|not a (good )?fit|not for us|"
                             r"we('re| are) (all )?set|pass on this)\b")
_INTERESTED = re.compile(r"\b(interest(ed|ing)?|demo|let'?s talk|can we talk|sounds good|book|schedule|"
                         r"call|meeting|yes)\b")
_QUESTION_START = re.compile(r"^\s*(what|how|why|when|where|which|who|does|do|can|could|is|are|will)\b")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DATE = re.compile(r"\b(?:in|on|from|after|until|till|by|early|mid|late|end of|back)\s+(" +
                         "|".join(_MONTHS) + r")\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_TODAY = re.compile(r"Today is (\d{4}-\d{2}-\d{2})")
_REPLY = re.compile(r"<reply>\n(.*)\n</reply>", re.DOTALL)
_KB = re.compile(r"<kb>\n(.*)\n</kb>", re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

_HUMAN = re.compile(r"\b(human|real person|a person|someone|somebody|agent|representative)\b")
_PRICE = re.compile(r"\b(price|prices|pricing|priced|cost|costs|quote|quotes|how much|discount|budget|fee|fees|"
                    r"rate|rates)\b|[$€£₹]")
_NOT_READY = re.compile(r"\b(not interested|not now|later|no thanks|not ready|not for us)\b")
_BOOK = re.compile(r"\b(book|booking|demo|call|meeting|schedule|meet)\b")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset({
    "the", "and", "for", "you", "your", "are", "was", "with", "what", "which", "does", "how", "can", "could",
    "would", "will", "who", "why", "when", "where", "this", "that", "there", "have", "has", "had", "our", "ours",
    "about", "any", "all", "from", "into", "out", "they", "them", "their", "its", "but", "not", "please",
    "tell", "more", "some", "use", "get", "want", "know", "like", "just", "also", "able", "is", "do", "me",
})


# ---------------------------------------------------------------------- helpers

def _today(text: str) -> date:
    m = _TODAY.search(text or "")
    try:
        return date.fromisoformat(m.group(1)) if m else date.today()
    except ValueError:
        return date.today()


def _first_date(text: str, today: date) -> Optional[str]:
    """An ISO date in the text, else "<month>" after in / on / until ... (the 1st of its next
    occurrence). YYYY-MM-DD or None."""
    m = _ISO_DATE.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = _MONTH_DATE.search(text)
    if m:
        month = _MONTHS.index(m.group(1)) + 1
        year = today.year if month > today.month else today.year + 1
        return date(year, month, 1).isoformat()
    return None


def _out(label: str, reason: str, **extra: Any) -> Dict[str, Any]:
    return {"label": label, "confidence": CONFIDENCE, "reason": f"local rule: {reason}",
            **{k: v for k, v in extra.items() if v}}


def _classify(user: str) -> Dict[str, Any]:
    m = _REPLY.search(user or "")
    text = (m.group(1) if m else user or "").strip()
    low = text.lower()
    today = _today(user)
    if _UNSUB.search(low):
        return _out("unsubscribe", "asks to stop contact")
    if _OOO.search(low):
        return _out("ooo", "out-of-office reply", return_date=_first_date(low, today))
    if _WRONG.search(low):
        email = _EMAIL.search(text)
        return _out("wrong_person", "not the right contact", referral_email=email.group(0) if email else None)
    if _NOT_NOW.search(low):
        return _out("not_now", "later", later_date=_first_date(low, today))
    if _NOT_INTERESTED.search(low):
        return _out("not_interested", "declines")
    if _INTERESTED.search(low):
        return _out("interested", "wants to talk")
    if "?" in low or _QUESTION_START.search(low):
        return _out("question", "asks a question",
                    draft_answer="Thanks for your question. A member of our team will follow up with details.")
    return _out("unsure", "no rule matched")


def _tokens(text: str) -> set:
    out = set()
    for w in _WORD.findall(text.lower()):
        if len(w) < 3 or w in _STOP:
            continue
        out.add(w[:-1] if len(w) > 4 and w.endswith("s") else w)
    return out


def _kb_entries(kb: str) -> List[Tuple[set, str]]:
    """(match words, answer text) per KB fact: each bullet line, and each FAQ Q/A pair (the
    question's words count; the answer is the A line). Headings and TODO lines are skipped."""
    entries: List[Tuple[set, str]] = []
    question: Optional[str] = None
    for raw in _COMMENT.sub("", kb).split("\n"):
        line = raw.strip().lstrip("-*").strip()
        if not line or line.startswith("#") or "TODO" in line:
            if "TODO" in line:
                question = None
            continue
        if line.startswith("Q:"):
            question = line[2:].strip()
            continue
        if line.startswith("A:"):
            answer = line[2:].strip()
            if answer:
                entries.append((_tokens(f"{question or ''} {answer}"), answer))
            question = None
            continue
        entries.append((_tokens(line), line))
    return entries


def _bot(system: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    users = [m.get("content") or "" for m in messages if m.get("role") == "user"]
    text = users[-1].strip().split("\n")[-1] if users else ""
    low = text.lower()
    if _HUMAN.search(low) or _PRICE.search(low):
        return {"kind": "wants_human"}
    if _NOT_READY.search(low):
        return {"kind": "not_ready"}
    if _BOOK.search(low):
        return {"kind": "ready_to_book"}
    m = _KB.search(system or "")
    words = _tokens(text)
    if not m or not words:
        return {"kind": "out_of_scope"}
    need = min(2, len(words))
    best, best_score = None, 0
    for key, answer in _kb_entries(m.group(1)):
        score = len(words & key)
        if score > best_score:
            best, best_score = answer, score
    if best is None or best_score < need:
        return {"kind": "out_of_scope"}
    return {"kind": "answer", "text": best}


class RuleBasedLLM:
    """LLMClient for local runs. Same signature and output dicts as the real providers."""

    def structured(self, *, system: str, messages: List[Dict[str, str]], tool_name: str,
                   tool_description: str, schema: Dict[str, Any], max_tokens: int,
                   model: str) -> Dict[str, Any]:
        if tool_name == "classify_reply":
            users = [m.get("content") or "" for m in messages if m.get("role") == "user"]
            return _classify(users[-1] if users else "")
        if tool_name == "bot_reply":
            return _bot(system, messages)
        raise LLMBadOutput("local fake llm: unknown tool")
