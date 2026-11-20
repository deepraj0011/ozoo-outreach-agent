"""Email schedule (task 26, simplified in task 27): the product manager decides when each email goes
and what kind it is; the agent still writes each email (templates/emails.yaml + the web app's
templates, per role and company).

One schedule (the default `send_plan` row) is a short list:

    {"start": {"mode": "on_add"} | {"mode": "days", "days": 2} | {"mode": "at", "at": "2026-11-24T11:00"},
     "emails": [{"kind": "standard"},
                {"kind": "auto", "days": 4, "fast": {"days": 2, "kind": "case_study"}},   # fast is optional
                ...],                                                                      # 1 to 4 emails
     "end_days": 5}

* Email 1 goes by `start`. Email N goes `days` after email N-1 was sent, in the next working-hours slot.
* `fast` (optional): if the lead showed interest since the previous email (a real click, a page read on
  our site, or a HOT score), that email goes sooner (`fast.days`) and as `fast.kind`; with
  `fast.last` it is also the last email (the sequence ends `end_days` after it). Interest that comes
  after the email was handed to the sender but before it went still switches it (`recheck`).
* After an out-of-office the next email goes no sooner than BACK_GAP_DAYS after the lead is back.
* After the last email, `end_days` later, the sequence ends (the usual route: nurture, HOT leads to sales).
* A reply, booking, unsubscribe or bounce still stops everything at once (rules.py), as always.

Runtime (worker job plan_schedule): a lead on the schedule has nothing to send until its next email
is decided. When that email's date has come it is rendered with its kind and handed to the tool with
due_at_<step> (sent in the next send-window slot, or at the exact time picked). The decision is written
to the lead's flags in the same transaction, so a repeated tick decides nothing twice. Per-lead
changes (send now, another date, pause, stop) live in flags[NEXT_FLAG] for the next email only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Event, Lead, SendPlan
from .score import band
from .sequencer import (
    LAST_STEP, PATH_FLAG, PLAN_FLAG, PLAN_VAR, SCHED_FLAG, SCHEDULE_DAYS, STEP_VARIANTS, _live_templates,
    _tracking_vars, render_step, step_offset,
)

logger = logging.getLogger(__name__)

KINDS = ("auto", *STEP_VARIANTS)
KIND_LABELS = {"auto": "Agent picks", "standard": "Use case + demo button", "case_study": "Case study",
               "product": "Product screen", "booking_slots": "Pick a meeting time"}
NOT_A_CLICK = ("unsubscribe", "trap")  # the unsubscribe page and the hidden scanner trap
START_MODES = ("on_add", "days", "at")
MAX_DAYS = 60
NAME = "Email schedule"
MAX_LOG = 30
NEXT_FLAG = "plan_next"   # the product manager's change to the next email: {"at", "kind", "hold", "end"}
LOG_FLAG = "plan_log"     # [{"at", "what"}]: decisions and changes, newest last
ENDED_FLAG = "plan_ended"
KINDS_FLAG = "plan_kinds"  # the kind each decided email went out as (one per our step)
START_FLAG = "plan_start"  # a start time for email 1 chosen at import (ISO, UTC)
BACK_FLAG = "plan_back_at"  # when the lead came back from an out-of-office (ISO, UTC)
BACK_GAP_DAYS = 1           # the next email goes at least this long after the lead is back
LAST_FLAG = "plan_last_step"  # interest made this email the last one ("sooner" with "last")
FAST_FLAG = "plan_fast"     # per decided email: it went sooner because the lead showed interest
_AT = "%Y-%m-%dT%H:%M"


class PlanError(ValueError):
    """A schedule that can't be saved, or a change that can't be made (message is shown to the user)."""


# ====================================================================== the schedule itself

def starter_plan() -> Dict[str, Any]:
    """The fixed schedule's days (0 / 4 / 9 / 15, then 5 days), with a case study 2 days after
    email 1 for a lead who showed interest."""
    return {"start": {"mode": "on_add"},
            "emails": [{"kind": "standard"},
                       {"kind": "auto", "days": 4, "fast": {"days": 2, "kind": "case_study"}},
                       {"kind": "auto", "days": 5},
                       {"kind": "auto", "days": 6}],
            "end_days": 5}


def _days(where: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise PlanError(f"{where}: enter a number of days.")
    try:
        n = float(value)
    except ValueError:
        raise PlanError(f"{where}: enter a number of days.") from None
    if n != int(n) or not 0 <= n <= MAX_DAYS:
        raise PlanError(f"{where}: days must be a whole number from 0 to {MAX_DAYS}.")
    return int(n)


def _local_at(where: str, value: Any) -> str:
    try:
        return datetime.strptime(str(value), _AT).strftime(_AT)
    except ValueError:
        raise PlanError(f"{where}: pick a date and time.") from None


def _kind(where: str, value: Any) -> str:
    if value not in KINDS:
        raise PlanError(f"{where}: choose what kind of email it is.")
    return value


def _clean_start(start: Any) -> Dict[str, Any]:
    start = start if isinstance(start, dict) else {}
    mode = start.get("mode", "on_add")
    if mode not in START_MODES:
        raise PlanError("Email 1: choose when it goes.")
    if mode == "days":
        return {"mode": "days", "days": _days("Email 1", start.get("days"))}
    if mode == "at":
        return {"mode": "at", "at": _local_at("Email 1", start.get("at"))}
    return {"mode": "on_add"}


def _clean_email(i: int, e: Any) -> Dict[str, Any]:
    where = f"Email {i + 1}"
    if not isinstance(e, dict):
        raise PlanError(f"{where}: missing.")
    out: Dict[str, Any] = {"kind": _kind(where, e.get("kind", "auto"))}
    if i == 0:
        return out
    out["days"] = _days(where, e.get("days"))
    fast = e.get("fast")
    if fast:
        if not isinstance(fast, dict):
            raise PlanError(f"{where}: bad \"sooner if interested\" setting.")
        days = _days(f"{where}, sooner if interested", fast.get("days"))
        if days > out["days"]:
            raise PlanError(f"{where}: \"sooner if interested\" can't be later than {out['days']} days.")
        out["fast"] = {"days": days, "kind": _kind(where, fast.get("kind", out["kind"]))}
        if fast.get("last"):
            out["fast"]["last"] = True
    return out


def clean_plan(data: Any) -> Dict[str, Any]:
    """The schedule as it is stored, or PlanError with a message for the screen."""
    if not isinstance(data, dict):
        raise PlanError("Send a schedule.")
    emails = data.get("emails")
    if not isinstance(emails, list) or not emails:
        raise PlanError("The schedule needs at least email 1.")
    if len(emails) > LAST_STEP:
        raise PlanError(f"At most {LAST_STEP} emails per lead.")
    return {"start": _clean_start(data.get("start")),
            "emails": [_clean_email(i, e) for i, e in enumerate(emails)],
            "end_days": _days("After the last email", data.get("end_days", 5))}


def plan_row(row: SendPlan) -> Dict[str, Any]:
    return {"id": row.id, "name": row.name, "data": row.data,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def _wait_days(branch: Dict[str, Any]) -> int:
    return int((branch.get("wait") or {}).get("days", 4))


def upgrade(data: Dict[str, Any]) -> Dict[str, Any]:
    """A plan saved by task 26's branching screen ("emails" was a tree of nodes) as a list: the
    "otherwise" path gives the emails and their days, the first other branch "sooner if interested"."""
    if not isinstance(data.get("emails"), dict):
        return data
    nodes, nid = data["emails"], data.get("first")
    out: List[Dict[str, Any]] = []
    prev: Optional[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]] = None
    end_days = 5
    while nid in nodes and len(out) < LAST_STEP:
        e: Dict[str, Any] = {"kind": nodes[nid].get("kind", "auto")}
        if prev is not None:
            other, quick = prev
            e["days"] = _wait_days(other)
            if quick is not None:
                e["fast"] = {"days": min(_wait_days(quick), e["days"]), "kind": nodes[quick["then"]].get("kind", "auto")}
        out.append(e)
        branches = nodes[nid].get("branches") or [{}]
        other = branches[-1]
        quick = next((b for b in branches[:-1] if b.get("then") in nodes), None)
        prev, nid, end_days = (other, quick), other.get("then"), _wait_days(other)
    return clean_plan({"start": data.get("start"), "emails": out, "end_days": end_days})


def current(session: Session) -> Optional[SendPlan]:
    """The saved schedule (the default plan), or None: the fixed days 0 / 4 / 9 / 15."""
    row = session.scalars(select(SendPlan).where(SendPlan.is_default.is_(True)).limit(1)).first()
    return _upgraded(row)


def _upgraded(row: Optional[SendPlan]) -> Optional[SendPlan]:
    if row is not None and isinstance((row.data or {}).get("emails"), dict):
        try:
            row.data = upgrade(dict(row.data))
        except PlanError:
            row.data = starter_plan()
    return row


def save_plan(session: Session, data: Dict[str, Any], now: datetime) -> SendPlan:
    """Save the schedule (there is one). It applies to new leads, and to each lead's next email
    that is not decided yet."""
    plan = clean_plan(data.get("data"))
    row = current(session)
    if row is None:
        row = SendPlan(name=NAME, data=plan, is_default=True, created_at=now, updated_at=now)
        session.add(row)
    row.data, row.updated_at = plan, now
    session.flush()
    logger.info("email schedule %s saved", row.id)
    return row


def _get(session: Session, plan_id: Any) -> Optional[SendPlan]:
    try:
        return session.get(SendPlan, int(plan_id))
    except (TypeError, ValueError):
        return None


def plan_for_lead(session: Session, lead: Lead) -> Optional[SendPlan]:
    """The lead's schedule row (kept on the lead), else the saved schedule, else None."""
    pid = (lead.variant_flags or {}).get(PLAN_FLAG)
    if pid is not None:
        row = _upgraded(_get(session, pid))
        if row is not None:
            return row
    return current(session)


# ====================================================================== deciding the next email

def zone(settings: Any) -> ZoneInfo:
    return ZoneInfo(getattr(settings, "business_tz", None) or "Asia/Kolkata")


def local_to_utc(value: str, settings: Any) -> datetime:
    return datetime.strptime(value, _AT).replace(tzinfo=zone(settings)).astimezone(timezone.utc)


def _scaled(days: int, settings: Any) -> timedelta:
    return timedelta(days=days) / float(getattr(settings, "time_scale", 1.0) or 1.0)


def _ts(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))


def interested(session: Session, lead: Lead, since: Optional[datetime]) -> bool:
    """Did the lead show interest since the previous email? A real person's click (not the unsubscribe
    page or the scanner trap) or page read on our site, or a HOT score. Scanners and opens never count."""
    if band(lead.score or 0) == "hot":
        return True
    stmt = select(Event.tag).where(Event.lead_id == lead.id, Event.kind.in_(("click", "visit")),
                                   Event.verdict == "human")
    if since is not None:
        stmt = stmt.where(Event.occurred_at >= since)
    return any(tag not in NOT_A_CLICK for tag in session.scalars(stmt))


def _start_due(plan: Dict[str, Any], lead: Lead, settings: Any) -> Tuple[datetime, bool]:
    flags = lead.variant_flags or {}
    picked = _ts(flags.get(START_FLAG))
    if picked is not None:
        return picked, True
    enrolled = _ts(flags.get("enrolled_at")) or _aware(lead.created_at) or datetime.now(timezone.utc)
    start = plan["start"]
    if start["mode"] == "at":
        return local_to_utc(start["at"], settings), True
    if start["mode"] == "days":
        return enrolled + _scaled(int(start["days"]), settings), False
    return enrolled, False


def next_email(session: Session, lead: Lead, plan: Dict[str, Any], settings: Any) -> Dict[str, Any]:
    """The lead's next email as things stand now:
    {"step" (0 = the sequence ends), "kind", "due", "exact", "fast" (the sooner date applies),
    "changed", "hold", "options"}. "options" = the normal and the sooner date, for the lead panel."""
    flags = lead.variant_flags or {}
    step = len(flags.get(PATH_FLAG) or [])
    change = flags.get(NEXT_FLAG) or {}
    emails = plan["emails"]
    options: List[Dict[str, Any]] = []
    fast = False
    if step == 0:
        due, exact = _start_due(plan, lead, settings)
        kind: Optional[str] = emails[0]["kind"]
        nxt = 1
    else:
        sent_at = _aware(lead.last_sent_at) or datetime.now(timezone.utc)
        last = flags.get(LAST_FLAG)
        if step >= len(emails) or step >= LAST_STEP or (isinstance(last, int) and step >= last):
            due, exact, kind, nxt = sent_at + _scaled(int(plan.get("end_days", 5)), settings), False, None, 0
        else:
            e = emails[step]
            due, exact, kind, nxt = sent_at + _scaled(int(e["days"]), settings), False, e["kind"], step + 1
            options.append({"when": "normal", "kind": kind, "due": due.isoformat()})
            if e.get("fast"):
                f_due = sent_at + _scaled(int(e["fast"]["days"]), settings)
                fast = interested(session, lead, sent_at)
                options.append({"when": "interested", "kind": e["fast"]["kind"], "due": f_due.isoformat(),
                                "applies": fast, "last": bool(e["fast"].get("last"))})
                if fast:
                    due, kind = f_due, e["fast"]["kind"]
        back = _ts(flags.get(BACK_FLAG))
        if back is not None and back > sent_at:  # back from an out-of-office: give them a day
            due = max(due, back + _scaled(BACK_GAP_DAYS, settings))
    if nxt and change.get("kind") in KINDS:
        kind = change["kind"]
    picked = _ts(change.get("at"))
    if picked is not None:
        due, exact = picked, True
    if change.get("end"):
        nxt, kind = 0, None
    last_after = bool(fast and nxt and step and emails[step].get("fast", {}).get("last"))
    return {"step": nxt, "kind": kind, "due": due, "exact": exact, "fast": fast, "last": last_after,
            "changed": bool(change.get("at") or change.get("kind")), "hold": bool(change.get("hold")),
            "options": options}


def log(lead: Lead, now: datetime, what: str) -> None:
    flags = dict(lead.variant_flags or {})
    flags[LOG_FLAG] = (list(flags.get(LOG_FLAG) or []) + [{"at": now.isoformat(), "what": what}])[-MAX_LOG:]
    lead.variant_flags = flags


def _set_flags(lead: Lead, **items: Any) -> None:
    flags = dict(lead.variant_flags or {})
    for k, v in items.items():
        if v is None:
            flags.pop(k, None)
        else:
            flags[k] = v
    lead.variant_flags = flags


def pending(lead: Lead) -> bool:
    """An email is decided and handed to the tool but not sent yet."""
    return len((lead.variant_flags or {}).get(PATH_FLAG) or []) > (lead.step_sent or 0)


def on_plan(lead: Lead) -> bool:
    f = lead.variant_flags or {}
    return f.get(PLAN_FLAG) is not None and not f.get(ENDED_FLAG)


def _push(session: Session, lead: Lead, tool: Any, step: int, kind: str, due: Optional[datetime], exact: bool,
          settings: Any) -> None:
    """Hand email `step` to the tool: its content (rendered now, with this kind) and its date.
    due None = paused (the tool waits)."""
    camp = step - step_offset(lead)
    if camp < 1:
        return
    rendered = render_step(lead, step, templates=_live_templates(session), settings=settings,
                           variant=None if kind == "auto" else kind)
    tool.update_variables(lead.tool_lead_id, {
        f"subject_{camp}": rendered[f"subject_{step}"], f"body_{camp}": rendered[f"body_{step}"],
        f"html_{camp}": rendered.get(f"html_{step}", ""), **_tracking_vars(lead, camp, step, settings),
        f"due_at_{camp}": due.isoformat() if due is not None else "hold", f"due_exact_{camp}": "1" if exact else ""})


def kind_label(kind: Optional[str]) -> str:
    return KIND_LABELS.get(kind or "", kind or "")


def commit_next(session: Session, lead: Lead, tool: Any, now: datetime, settings: Any) -> str:
    """Decide the lead's next email if its date has come: "sent_to_tool", "end" (the caller finishes
    the sequence: the usual end-of-sequence route), or "waiting"."""
    row = plan_for_lead(session, lead)
    if row is None or (lead.variant_flags or {}).get(ENDED_FLAG):
        return "waiting"
    if pending(lead):
        return recheck(session, lead, tool, row.data, now, settings)
    nxt = next_email(session, lead, row.data, settings)
    if nxt["hold"]:
        return "waiting"
    flags = lead.variant_flags or {}
    if not nxt["step"]:
        if nxt["due"] > now and not (flags.get(NEXT_FLAG) or {}).get("end"):
            return "waiting"
        _set_flags(lead, **{ENDED_FLAG: now.isoformat(), NEXT_FLAG: None})
        log(lead, now, "No more emails: the sequence finished")
        return "end"
    step = nxt["step"]
    if step > 1 and nxt["due"] > now:  # email 1 is decided at once; the tool holds it until its date
        return "waiting"
    _push(session, lead, tool, step, nxt["kind"], nxt["due"], nxt["exact"], settings)
    path = list(flags.get(PATH_FLAG) or []) + [f"e{step}"]
    kinds = (list(flags.get(KINDS_FLAG) or []) + [None] * len(path))[:len(path) - 1] + [nxt["kind"]]
    fasts = (list(flags.get(FAST_FLAG) or []) + [False] * len(path))[:len(path) - 1] + [bool(nxt["fast"])]
    _set_flags(lead, **{PATH_FLAG: path, KINDS_FLAG: kinds, FAST_FLAG: fasts, NEXT_FLAG: None,
                        LAST_FLAG: step if nxt["last"] else flags.get(LAST_FLAG),
                        SCHED_FLAG: {"step": step, "kind": nxt["kind"], "due": nxt["due"].isoformat(),
                                     "exact": nxt["exact"], "planned_due": nxt["due"].isoformat(),
                                     "fast": bool(nxt["fast"]), "changed": bool(nxt["changed"])}})
    why = " (your change)" if nxt["changed"] else " (sooner: the lead showed interest)" if nxt["fast"] else ""
    why += "; the last email" if nxt["last"] else ""
    log(lead, now, f"Email {step} scheduled: {kind_label(nxt['kind'])}{why}")
    return "sent_to_tool"


def recheck(session: Session, lead: Lead, tool: Any, plan: Dict[str, Any], now: datetime, settings: Any) -> str:
    """The next email is with the sender but not sent yet (e.g. decided in the evening for the
    morning slot). If the lead showed interest since the previous email and that email has a
    "sooner if interested" type, switch it now. Your own changes and paused emails are left alone."""
    flags = lead.variant_flags or {}
    sched = dict(flags.get(SCHED_FLAG) or {})
    step = int(sched.get("step") or 0)
    if step < 2 or sched.get("fast") or sched.get("changed") or (flags.get(NEXT_FLAG) or {}):
        return "waiting"
    emails = plan["emails"]
    fast = emails[step - 1].get("fast") if step - 1 < len(emails) else None
    if not fast or not interested(session, lead, _aware(lead.last_sent_at)):
        return "waiting"
    due = _ts(sched.get("due")) or now
    _push(session, lead, tool, step, fast["kind"], due, bool(sched.get("exact")), settings)
    sched.update(kind=fast["kind"], fast=True)
    kinds = list(flags.get(KINDS_FLAG) or [])
    fasts = list(flags.get(FAST_FLAG) or [])
    if len(kinds) >= step:
        kinds[step - 1] = fast["kind"]
    if len(fasts) >= step:
        fasts[step - 1] = True
    _set_flags(lead, **{SCHED_FLAG: sched, KINDS_FLAG: kinds, FAST_FLAG: fasts,
                        LAST_FLAG: step if fast.get("last") else flags.get(LAST_FLAG)})
    log(lead, now, f"Email {step} switched to {kind_label(fast['kind'])}: the lead showed interest before it went"
                   + ("; the last email" if fast.get("last") else ""))
    return "sent_to_tool"


# ====================================================================== the product manager's changes

ACTIONS = ("send_now", "move", "kind", "hold", "resume", "clear", "end", "use_plan")


def change_next(session: Session, lead: Lead, tool: Any, action: str, now: datetime, settings: Any, *,
                at: Optional[str] = None, days: Any = None, kind: Optional[str] = None) -> str:
    """One change to the lead's next email (lead panel). Returns a sentence for the change log."""
    if action not in ACTIONS:
        raise PlanError("Unknown change.")
    if lead.state != "in_sequence" or not lead.tool_lead_id:
        raise PlanError("This lead is not in its email sequence any more.")
    if action == "use_plan":
        what = _use_plan(session, lead, tool)
        log(lead, now, what)
        return what
    if not on_plan(lead):
        raise PlanError("This lead follows the fixed days. Switch it to the schedule first.")
    change = dict((lead.variant_flags or {}).get(NEXT_FLAG) or {})
    if action == "send_now":
        change.update(at=now.isoformat())
        change.pop("hold", None)
        what = "You sent the next email now"
    elif action == "move":
        when = _pick_time(at, days, now, settings)
        change.update(at=when.isoformat())
        change.pop("hold", None)
        what = f"You moved the next email to {when.astimezone(zone(settings)).strftime('%a %d %b, %H:%M')}"
    elif action == "kind":
        change["kind"] = _kind("Next email", kind)
        what = f"You changed the next email to: {kind_label(kind)}"
    elif action == "hold":
        change["hold"] = True
        what = "You paused the emails"
    elif action == "resume":
        change.pop("hold", None)
        what = "You resumed the emails"
    elif action == "end":
        change = {"end": True}
        what = "You stopped the emails for this lead"
    else:  # clear
        change = {}
        what = "You undid your changes: the schedule decides again"
    _set_flags(lead, **{NEXT_FLAG: change or None})
    if pending(lead):
        _repush(session, lead, tool, change, settings)
    log(lead, now, what)
    return what


def _pick_time(at: Optional[str], days: Any, now: datetime, settings: Any) -> datetime:
    when = local_to_utc(_local_at("Date", at), settings) if at else now + _scaled(_days("Days", days), settings)
    return max(when, now)


def _repush(session: Session, lead: Lead, tool: Any, change: Dict[str, Any], settings: Any) -> None:
    """The decided email is still waiting in the tool: give it the changed date / kind / pause. Stop
    takes it back (it is never sent) so the plan job finishes the sequence."""
    flags = lead.variant_flags or {}
    sched = dict(flags.get(SCHED_FLAG) or {})
    step = int(sched.get("step") or (lead.step_sent or 0) + 1)
    if change.get("end"):
        tool.update_variables(lead.tool_lead_id, {f"due_at_{step - step_offset(lead)}": "hold"})
        _set_flags(lead, **{PATH_FLAG: list(flags.get(PATH_FLAG) or [])[:lead.step_sent or 0], SCHED_FLAG: None})
        return
    kind = change["kind"] if change.get("kind") in KINDS else sched.get("kind", "auto")
    picked = _ts(change.get("at"))
    due = picked or _ts(sched.get("planned_due")) or datetime.now(timezone.utc)
    exact = True if picked else bool(sched.get("exact"))
    _push(session, lead, tool, step, kind, None if change.get("hold") else due, exact, settings)
    sched.update(kind=kind, due=due.isoformat(), exact=exact, changed=bool(change))
    kinds = list(flags.get(KINDS_FLAG) or [])
    if len(kinds) >= step:
        kinds[step - 1] = kind
    _set_flags(lead, **{SCHED_FLAG: sched, KINDS_FLAG: kinds})


def _use_plan(session: Session, lead: Lead, tool: Any) -> str:
    """Put a lead on the fixed days onto the saved schedule, from its next email (the emails it
    already got count as the schedule's first ones)."""
    row = current(session)
    if row is None:
        raise PlanError("Save the email schedule first.")
    if (lead.variant_flags or {}).get(PLAN_FLAG) is None:
        if not getattr(tool, "SUPPORTS_SCHEDULE", False):
            raise PlanError("The schedule needs our own sender (Gmail / mailbox mode); Instantly keeps its own.")
        if (lead.step_sent or 0) >= LAST_STEP:
            raise PlanError("This lead already got all its emails.")
        tool.update_variables(lead.tool_lead_id, {PLAN_VAR: "1"})
        _set_flags(lead, **{PATH_FLAG: ["_"] * (lead.step_sent or 0)})
    _set_flags(lead, **{PLAN_FLAG: row.id, NEXT_FLAG: None, ENDED_FLAG: None})
    return "You switched this lead to the email schedule"


# ====================================================================== views for the web app

def _fixed_next(lead: Lead, settings: Any) -> Optional[Dict[str, Any]]:
    if not lead.tool_lead_id or lead.state != "in_sequence" or (lead.step_sent or 0) >= LAST_STEP:
        return None
    enrolled = _ts((lead.variant_flags or {}).get("enrolled_at"))
    nxt = (lead.step_sent or 0) + 1
    return {"step": nxt, "due": (enrolled + _scaled(SCHEDULE_DAYS[nxt], settings)).isoformat() if enrolled else None}


def _sent_kind(flags: Dict[str, Any], i: int, plan: Dict[str, Any]) -> Optional[str]:
    kinds = flags.get(KINDS_FLAG) or []
    if i < len(kinds) and kinds[i]:
        return kinds[i]
    return plan["emails"][i]["kind"] if i < len(plan["emails"]) else None


def _flag_at(flags: Dict[str, Any], key: str, i: int) -> bool:
    items = flags.get(key) or []
    return bool(items[i]) if i < len(items) else False


def _cut(flags: Dict[str, Any], i: int) -> bool:
    """Email i (0-based) won't go: interest made an earlier one the last."""
    last = flags.get(LAST_FLAG)
    return isinstance(last, int) and i >= last


def lead_view(session: Session, lead: Lead, settings: Any) -> Dict[str, Any]:
    """The lead panel's Email schedule tab: emails so far, the next one, the ones after it."""
    flags = lead.variant_flags or {}
    base: Dict[str, Any] = {"on_plan": False, "fixed": None, "log": list(reversed(flags.get(LOG_FLAG) or [])),
                            "step_sent": lead.step_sent or 0, "in_sequence": lead.state == "in_sequence"}
    row = _upgraded(_get(session, flags.get(PLAN_FLAG))) if flags.get(PLAN_FLAG) is not None else None
    if row is None:
        base["fixed"] = _fixed_next(lead, settings)
        return base
    plan = row.data
    path = list(flags.get(PATH_FLAG) or [])
    out = {**base, "on_plan": True, "ended": flags.get(ENDED_FLAG),
           "path": [{"step": i + 1, "kind": _sent_kind(flags, i, plan), "sooner": _flag_at(flags, FAST_FLAG, i)}
                    for i in range(len(path))],
           "later": [{"step": i + 1, "kind": e["kind"], "days": e.get("days")}
                     for i, e in enumerate(plan["emails"]) if i > len(path) and not _cut(flags, i)],
           "last_step": flags.get(LAST_FLAG),
           "interest_now": bool(lead.step_sent) and interested(session, lead, _aware(lead.last_sent_at)),
           "back_at": flags.get(BACK_FLAG),
           "change": flags.get(NEXT_FLAG) or {}, "next": None, "pending": None}
    if flags.get(ENDED_FLAG) or lead.state != "in_sequence":
        out["later"] = []
        return out
    if pending(lead):
        out["pending"] = {**(flags.get(SCHED_FLAG) or {}), "hold": bool((flags.get(NEXT_FLAG) or {}).get("hold"))}
        return out
    nxt = next_email(session, lead, plan, settings)
    out["next"] = {**nxt, "due": nxt["due"].isoformat()}
    return out


def upcoming(session: Session, settings: Any, limit: int = 500) -> List[Dict[str, Any]]:
    """Every lead's next email, for the calendar: schedule leads from the schedule, the others from
    the fixed days (dates before the send window moves them into working hours)."""
    leads = session.scalars(select(Lead).where(Lead.state == "in_sequence", Lead.tool_lead_id.is_not(None))
                            .order_by(Lead.id).limit(limit))
    out: List[Dict[str, Any]] = []
    for ld in leads:
        v = lead_view(session, ld, settings)
        item = {"lead_id": ld.id, "name": ld.name, "company": ld.company, "fixed": not v["on_plan"]}
        if v.get("pending"):
            p = v["pending"]
            out.append({**item, "step": p.get("step"), "kind": p.get("kind"), "due": p.get("due"),
                        "hold": p.get("hold"), "decided": True})
        elif v.get("next") and v["next"]["step"]:
            n = v["next"]
            out.append({**item, "step": n["step"], "kind": n["kind"], "due": n["due"], "hold": n["hold"],
                        "decided": False})
        elif v.get("fixed") and v["fixed"]["due"]:
            out.append({**item, "step": v["fixed"]["step"], "kind": "auto", "due": v["fixed"]["due"],
                        "hold": False, "decided": False})
    return sorted(out, key=lambda x: x["due"] or "")


def options() -> Dict[str, Any]:
    return {"kinds": [{"id": k, "label": KIND_LABELS[k]} for k in KINDS], "max_emails": LAST_STEP,
            "max_days": MAX_DAYS, "starter": starter_plan()}


def view(session: Session) -> Dict[str, Any]:
    """The schedule screen: the saved schedule (None = not saved: fixed days) and leads following it."""
    row = current(session)
    following = 0
    if row is not None:
        for ld in session.scalars(select(Lead).where(Lead.state == "in_sequence")):
            if on_plan(ld):
                following += 1
    return {"plan": plan_row(row) if row else None, "following": following}


def due_ids(session: Session, limit: int) -> List[int]:
    """Leads the plan job looks at: in sequence by email, on the schedule (a decided-but-unsent email
    is re-checked for interest)."""
    out: List[int] = []
    for ld in session.scalars(select(Lead).where(Lead.state == "in_sequence", Lead.active_channel == "email",
                                                 Lead.tool_lead_id.is_not(None)).order_by(Lead.id)):
        if on_plan(ld):
            out.append(ld.id)
            if len(out) >= limit:
                break
    return out
