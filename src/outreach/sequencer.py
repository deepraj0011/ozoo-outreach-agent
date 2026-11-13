"""Email sequencer: template rendering with tracked links, tool enrolment, end-of-sequence routing."""
from __future__ import annotations

import logging
import re
import string
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from sqlalchemy.orm import Session

from .actions import Action, Alert, SetState
from .config import Settings, get_settings
from .links import make_link
from .models import Lead
from .score import band
from .states import LeadState

logger = logging.getLogger(__name__)

SCHEDULE_DAYS = {1: 0, 2: 4, 3: 9, 4: 15}
END_WAIT_DAYS = 5
VARIANT_PRIORITY = ("booking_slots", "case_study", "product")
STEP_VARIANTS = ("standard", "case_study", "product", "booking_slots")
ALLOWED_PLACEHOLDERS = frozenset({
    "first_name", "company", "booking_link", "unsubscribe_link", "pricing_link",
    "case_study_link", "product_link", "homepage_link",
})
REPLY_KEYS = ("booking_link", "booking_slots", "reschedule")
MAX_VALUE_LEN = 80
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATES = REPO_ROOT / "templates" / "emails.yaml"

_cache: Dict[str, Dict[str, Any]] = {}
_WS = re.compile(r"[\r\n\t\v\f\x00-\x1f\x7f  ]+")


class SequencerConfigError(Exception):
    pass


def _fields(text: str) -> List[str]:
    try:
        return [f for _, f, _, _ in string.Formatter().parse(text) if f is not None]
    except ValueError as exc:
        raise ValueError(f"bad template syntax: {exc}") from exc


def _check_text(where: str, text: Any, need_links: bool) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"template {where} is missing or empty")
    fields = _fields(text)
    for f in fields:
        if f not in ALLOWED_PLACEHOLDERS:
            raise ValueError(f"template {where} uses unknown placeholder {{{f}}}")
    if need_links:
        for req in ("booking_link", "unsubscribe_link"):
            if req not in fields:
                raise ValueError(f"template {where} must contain {{{req}}}")


def _validate(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict) or "steps" not in data or "replies" not in data:
        raise ValueError("templates need 'steps' and 'replies'")
    steps = data["steps"]
    for step in SCHEDULE_DAYS:
        if not isinstance(steps, dict) or step not in steps:
            raise ValueError(f"missing step {step}")
        block = steps[step]
        for v in STEP_VARIANTS:
            if not isinstance(block, dict) or v not in block:
                raise ValueError(f"missing variant {v} in step {step}")
            _check_text(f"steps.{step}.{v}.subject", block[v].get("subject"), False)
            _check_text(f"steps.{step}.{v}.body", block[v].get("body"), True)
    for key in REPLY_KEYS:
        if key not in data["replies"]:
            raise ValueError(f"missing reply {key}")
        _check_text(f"replies.{key}.body", data["replies"][key].get("body"), True)
    return data


def load_templates(path: Optional[str] = None) -> Dict[str, Any]:
    p = str(Path(path) if path else DEFAULT_TEMPLATES)
    if p not in _cache:
        with open(p, encoding="utf-8") as fh:
            _cache[p] = _validate(yaml.safe_load(fh))
    return _cache[p]


def _clean(value: Optional[str]) -> str:
    return _WS.sub(" ", value or "").strip()[:MAX_VALUE_LEN].strip()


def _values(lead: Lead, step: int, settings: Settings) -> Dict[str, str]:
    def link(tag: str) -> str:
        return make_link(lead.id, step, tag, "email", settings=settings)

    name = _clean(lead.name)
    return {
        "first_name": name.split(" ")[0] if name else "",
        "company": _clean(lead.company),
        "booking_link": link("booking"),
        "unsubscribe_link": link("unsubscribe"),
        "pricing_link": link("pricing"),
        "case_study_link": link("case_study"),
        "product_link": link("product"),
        "homepage_link": link("homepage"),
    }


def _variant(lead: Lead) -> str:
    present = (lead.variant_flags or {}).get("variants", [])
    return next((v for v in VARIANT_PRIORITY if v in present), "standard")


def render_step(
    lead: Lead, step: int, *, templates: Optional[Dict[str, Any]] = None, settings: Optional[Settings] = None
) -> Dict[str, str]:
    t = templates or load_templates()
    s = settings or get_settings()
    block = t["steps"][step]
    variant = block[_variant(lead)]
    vals = _values(lead, step, s)
    subject = _clean(variant["subject"].format_map(vals))  # single line by construction
    return {f"subject_{step}": subject, f"body_{step}": variant["body"].format_map(vals)}


def render_reply(
    lead: Lead, key: str, *, templates: Optional[Dict[str, Any]] = None, settings: Optional[Settings] = None
) -> str:
    t = templates or load_templates()
    s = settings or get_settings()
    return t["replies"][key]["body"].format_map(_values(lead, lead.step_sent, s))


LAST_STEP = max(SCHEDULE_DAYS)
STEP_OFFSET = "step_offset"


def step_offset(lead: Lead) -> int:
    """Campaign step + offset = our step. Non-zero only after a mid-sequence re-enrolment."""
    try:
        return int((lead.variant_flags or {}).get(STEP_OFFSET, 0))
    except (TypeError, ValueError):
        return 0


def enroll(
    session: Session, lead: Lead, tool: Any, now: datetime, *,
    settings: Optional[Settings] = None, templates: Optional[Dict[str, Any]] = None, from_step: int = 1,
) -> None:
    """Add the lead to its campaign. from_step > 1 re-enrols mid-sequence (after an out-of-office
    resume: the earlier reply deleted the tool lead). The campaign always starts at its step 1, so
    campaign step i carries our step from_step - 1 + i and variant_flags["step_offset"] records the
    shift (rules map the tool's `sent` step back). Campaign steps past our last step repeat the last
    one (never blank); the tool lead is stopped as soon as our last step is reported sent. The
    campaign's own day spacing (0 / 4 / 9 / 15) applies from the re-enrolment."""
    if lead.tool_lead_id:
        return
    if from_step not in SCHEDULE_DAYS:
        raise ValueError(f"from_step must be one of {sorted(SCHEDULE_DAYS)}")
    s = settings or get_settings()
    group = lead.timezone_group or s.default_tz_group
    campaign_id = s.campaign_ids.get(group)
    if not campaign_id:
        raise SequencerConfigError(f"no campaign configured for timezone group {group!r}")
    t = templates or load_templates()
    offset = from_step - 1
    variables: Dict[str, str] = {}
    for step in SCHEDULE_DAYS:
        real = min(step + offset, LAST_STEP)
        rendered = render_step(lead, real, templates=t, settings=s)
        variables[f"subject_{step}"] = rendered[f"subject_{real}"]
        variables[f"body_{step}"] = rendered[f"body_{real}"]
    vals = _values(lead, 1, s)
    variables["first_name"] = vals["first_name"]
    variables["company"] = vals["company"]
    lead.tool_lead_id = tool.add_lead(campaign_id, lead, variables)
    flags = dict(lead.variant_flags or {})
    flags["enrolled_at"] = now.isoformat()
    if offset:
        flags[STEP_OFFSET] = offset
    else:
        flags.pop(STEP_OFFSET, None)
    lead.variant_flags = flags
    session.flush()
    logger.info("enrolled lead_id=%s from step %s", lead.id, from_step)


def refresh_next_step(
    session: Session, lead: Lead, tool: Any, *,
    settings: Optional[Settings] = None, templates: Optional[Dict[str, Any]] = None,
) -> None:
    if not lead.tool_lead_id or lead.step_sent >= LAST_STEP:
        return
    real = lead.step_sent + 1
    campaign_step = real - step_offset(lead)
    if campaign_step < 1:
        return
    rendered = render_step(lead, real, templates=templates, settings=settings)
    tool.update_variables(lead.tool_lead_id, {f"subject_{campaign_step}": rendered[f"subject_{real}"],
                                              f"body_{campaign_step}": rendered[f"body_{real}"]})


def end_of_sequence_route(lead: Lead, human_clicks: int) -> Tuple[str, List[Action]]:
    """After Email 4 + 5 days with no reply. Opens are not used (unreliable), and there is
    no WhatsApp step here: opted-in leads already got WhatsApp on day 1."""
    if band(lead.score) == "hot":
        return "sales_personal", [
            SetState(LeadState.NURTURE, "end_hot"),
            Alert("hot_no_reply", "Hot lead finished the sequence without replying"),
        ]
    if human_clicks > 0:
        return "warm_2_3w", [SetState(LeadState.NURTURE, "end_clicked")]
    return "monthly", [SetState(LeadState.NURTURE, "end_silent")]
