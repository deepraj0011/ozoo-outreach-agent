"""What the agent concluded about each simulated lead, next to what the person really did.

Outcome categories (one per lead, the first that applies):
  booked        a demo was booked
  unsubscribed  the lead opted out
  bounced       the email bounced
  referred      a "wrong person" reply named a colleague
  ooo           an out-of-office reply paused the lead
  not_now       "not now": the lead went to nurture
  interested    replied interested, not booked yet
  hot           sales got a hot-lead alert (a verified visit or a strong human signal)
  check         sales got a "clicks only, check first" alert
  silent        nothing for sales
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, List

from sqlalchemy import select

from ..models import Alert as AlertRow
from ..models import Event, Lead
from .engine import SimLead, World

SITE_TAGS = ("homepage", "pricing", "case_study", "product", "booking")


@dataclass
class LeadResult:
    sim: SimLead
    state: str
    score: int
    emails_sent: int
    opens: Counter
    clicks: Counter
    visits: int
    labels: List[str]
    alerts: List[str]
    outcome: str

    @property
    def ok(self) -> bool:
        return self.outcome == self.sim.persona.expect


def _outcome(state: str, events: List[Event], labels: List[str], alerts: List[str]) -> str:
    if state in ("booked", "handed_off"):
        return "booked"
    kinds = {e.kind for e in events}
    if "bounce_hard" in kinds:
        return "bounced"
    if state == "suppressed" or "unsubscribe" in kinds:
        return "unsubscribed"
    for label, out in (("wrong_person", "referred"), ("ooo", "ooo"), ("not_now", "not_now"),
                       ("interested", "interested")):
        if label in labels:
            return out
    if "hot_lead" in alerts:
        return "hot"
    if "click_check" in alerts:
        return "check"
    return "silent"


def collect(world: World) -> List[LeadResult]:
    out = []
    with world.factory() as s:
        for sim in world.leads:
            lead = s.get(Lead, sim.lead_id)
            events = list(s.scalars(select(Event).where(Event.lead_id == sim.lead_id).order_by(Event.id)))
            alerts = [a.kind for a in s.scalars(select(AlertRow).where(AlertRow.lead_id == sim.lead_id))]
            labels = [str(((e.data or {}).get("classification") or {}).get("label")) for e in events
                      if e.kind == "reply" and (e.data or {}).get("classification")]
            opens = Counter(e.verdict or "pending" for e in events if e.kind == "open")
            clicks = Counter(e.verdict or "pending" for e in events if e.kind == "click" and e.tag in SITE_TAGS)
            visits = sum(1 for e in events if e.kind == "visit" and e.verdict == "human")
            sent = sum(1 for e in events if e.kind == "sent")
            out.append(LeadResult(sim, lead.state, lead.score or 0, sent, opens, clicks, visits, labels, alerts,
                                  _outcome(lead.state, events, labels, alerts)))
    return out


def _n(c: Counter, *keys: str) -> int:
    return sum(c.get(k, 0) for k in keys)


def summary(results: List[LeadResult]) -> Dict[str, int]:
    interested = [r for r in results if r.sim.persona.interested]
    return {
        "leads": len(results),
        "emails_sent": sum(r.emails_sent for r in results),
        "emails_really_read": sum(len(r.sim.persona.reads) for r in results),
        "people_who_read_any": sum(1 for r in results if r.sim.persona.reads),
        "opens_logged": sum(sum(r.opens.values()) for r in results),
        "opens_counted_human": sum(_n(r.opens, "human") for r in results),
        "opens_machine_or_bot": sum(_n(r.opens, "machine", "bot") for r in results),
        "clicks_logged": sum(sum(r.clicks.values()) for r in results),
        "clicks_by_people": sum(r.sim.person_clicks for r in results),
        "clicks_by_machines": sum(r.sim.machine_clicks for r in results),
        "clicks_counted_human": sum(_n(r.clicks, "human") for r in results),
        "engaged_visits": sum(r.visits for r in results),
        "replies": sum(len(r.labels) for r in results),
        "meetings_booked": sum(1 for r in results if r.outcome == "booked"),
        "sales_alerts_hot": sum(1 for r in results if "hot_lead" in r.alerts),
        "sales_alerts_hot_wrong": sum(1 for r in results if "hot_lead" in r.alerts and not r.sim.persona.interested),
        "check_alerts": sum(1 for r in results if "click_check" in r.alerts),
        "check_alerts_on_machines": sum(1 for r in results if "click_check" in r.alerts and r.sim.person_clicks == 0),
        "really_interested": len(interested),
        "really_interested_caught": sum(1 for r in interested if r.outcome in ("booked", "hot", "interested")),
        "outcome_as_expected": sum(1 for r in results if r.ok),
    }


def _fmt_counter(c: Counter) -> str:
    return ", ".join(f"{v} {k}" for k, v in sorted(c.items())) or "none"


def render(results: List[LeadResult], *, detailed: bool = True) -> str:
    s = summary(results)
    lines = [
        "# Realistic lead run: what really happened vs what the agent concluded",
        "",
        "Simulated leads went through the real agent (intake, our own sender with its send window, "
        "tracked links, open image, visit beacon, webhooks, worker, AI reply reading with the local "
        "rule-based model). Each lead behaves like a real person, and its inbox brings its own "
        "machines (Apple Mail, Microsoft Defender, bank security sandboxes).",
        "",
        "## Summary",
        "",
        "| | |",
        "|---|---|",
        f"| Leads | {s['leads']} |",
        f"| Emails sent | {s['emails_sent']} |",
        f"| Emails a person really read | {s['emails_really_read']} (people who read anything: "
        f"{s['people_who_read_any']} of {s['leads']}) |",
        f"| Opens logged / counted as a person / machine or bot | {s['opens_logged']} / "
        f"{s['opens_counted_human']} / {s['opens_machine_or_bot']} |",
        f"| Link clicks: by people / by machines | {s['clicks_by_people']} / {s['clicks_by_machines']} |",
        f"| Clicks the agent counted as a person | {s['clicks_counted_human']} |",
        f"| Engaged page visits (verified reading) | {s['engaged_visits']} |",
        f"| Replies read by the AI | {s['replies']} |",
        f"| Meetings booked | {s['meetings_booked']} |",
        f"| Hot-lead alerts to sales / of which wrong | {s['sales_alerts_hot']} / {s['sales_alerts_hot_wrong']} |",
        f"| 'Check first' alerts / of which on machines only | {s['check_alerts']} / "
        f"{s['check_alerts_on_machines']} |",
        f"| Really interested leads caught | {s['really_interested_caught']} of {s['really_interested']} |",
        f"| Outcome as expected | {s['outcome_as_expected']} of {s['leads']} |",
        "",
    ]
    if not detailed:
        return "\n".join(lines)
    lines += ["## Per lead", "",
              "| # | Lead (truth) | Inbox | Emails read / sent | Agent saw | Alerts | Outcome | Expected |",
              "|---|---|---|---|---|---|---|---|"]
    for r in results:
        p = r.sim.persona
        saw = (f"opens: {_fmt_counter(r.opens)}; clicks: {_fmt_counter(r.clicks)}; visits: {r.visits}; "
               f"replies: {', '.join(r.labels) or 'none'}; score {r.score}")
        mark = "✅" if r.ok else "❌"
        lines.append(f"| {r.sim.lead_id} | **{p.title}**: {p.story} | {p.inbox.name} | {len(p.reads)} / "
                     f"{r.emails_sent} | {saw} | {', '.join(sorted(set(r.alerts))) or 'none'} | {r.outcome} | "
                     f"{mark} {p.expect}{(' (' + p.expect_note + ')') if p.expect_note else ''} |")
    lines += ["", "## Timelines", ""]
    for r in results:
        lines.append(f"**Lead {r.sim.lead_id}: {r.sim.persona.title}** ({r.state}, score {r.score})")
        lines += [f"- {entry}" for entry in r.sim.log] or ["- nothing happened"]
        lines.append("")
    return "\n".join(lines)
