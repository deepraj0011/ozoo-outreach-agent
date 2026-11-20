"""Email sequencer: template rendering with tracked links, tool enrolment, end-of-sequence routing."""
from __future__ import annotations

import hashlib
import logging
import re
import string
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from sqlalchemy.orm import Session

from .actions import Action, Alert, SetState, ToolStop
from .config import Settings, get_settings
from .links import make_link, make_open_pixel, make_trap_url, make_unsubscribe_url
from .models import Lead
from .richmail import MARKERS, image_file, rich_parts, validate_rich
from .score import band
from .states import LeadState

logger = logging.getLogger(__name__)

SCHEDULE_DAYS = {1: 0, 2: 4, 3: 9, 4: 15}
END_WAIT_DAYS = 5  # = config.DELAY_DEFAULTS_S["end_of_sequence_wait"] (docs only; the worker reads config)
VARIANT_PRIORITY = ("booking_slots", "case_study", "product")
STEP_VARIANTS = ("standard", "case_study", "product", "booking_slots")
# Per-lead angle texts (templates `angles:`), chosen from the lead's role / industry (task 20).
ANGLE_FIELDS = ("topic", "hook", "value", "proof", "ask", "usecase")
ALLOWED_PLACEHOLDERS = frozenset({
    "first_name", "company", "booking_link", "unsubscribe_link", "pricing_link",
    "case_study_link", "product_link", "voice_link", "chat_link", "trial_link", "homepage_link", "signature",
    "angle_link",  # the product page that fits the lead's angle (angles.<name>.page)
    *ANGLE_FIELDS,
    "answer", "follow_up_when",  # replies only (question_answer, not_now_ack)
})
# Replies with a booking link (and so an unsubscribe link); the courtesy replies carry neither.
REPLY_KEYS = ("booking_link", "booking_slots", "reschedule", "question_answer", "question_ack")
COURTESY_REPLY_KEYS = ("not_now_ack", "not_interested_ack", "referral_thanks", "who_is_right", "nurture_checkin")
DEFAULT_ANGLE = "general"
ANGLE_PAGES = ("product", "voice", "chat", "pricing", "case_study", "homepage")
MAX_VALUE_LEN = 80
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATES = REPO_ROOT / "templates" / "emails.yaml"

_cache: Dict[str, Dict[str, Any]] = {}
_WS = re.compile(r"[\r\n\t\v\f\x00-\x1f\x7f  ]+")


class SequencerConfigError(Exception):
    pass


def _fields(text: str) -> List[str]:
    """The {placeholders} in `text`. Plain names only: a format spec ({name:>9999}) or a conversion
    ({name!r}) is refused (it could blow a text up, and nested specs are not checked)."""
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError as exc:
        raise ValueError(f"bad template syntax: {exc}") from exc
    for _, field, spec, conv in parsed:
        if field is not None and (spec or conv):
            raise ValueError(f"bad template syntax: {{{field}}} must be a plain placeholder")
    return [f for _, f, _, _ in parsed if f is not None]


def _check_text(where: str, text: Any, need_links: bool) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"template {where} is missing or empty")
    fields = _fields(text)
    for f in fields:
        if f not in ALLOWED_PLACEHOLDERS:
            raise ValueError(f"template {where} uses unknown placeholder {{{f}}}")
    if need_links:
        for req in ("booking_link", "unsubscribe_link"):
            if req == "booking_link" and "[[button]]" in text:
                continue  # the button is the booking link (and a "Book …: <link>" line in the text)
            if req not in fields:
                raise ValueError(f"template {where} must contain {{{req}}}")


def _alternatives(where: str, value: Any) -> List[str]:
    """A text, or a list of alternative texts (one is picked per lead)."""
    items = value if isinstance(value, list) else [value]
    if not items:
        raise ValueError(f"template {where} has an empty list")
    return items


def _check_any(where: str, value: Any, need_links: bool) -> None:
    for i, text in enumerate(_alternatives(where, value)):
        _check_text(f"{where}[{i}]", text, need_links)


def _validate_angles(data: Dict[str, Any]) -> None:
    angles = data.get("angles")
    if angles is None:
        return
    if not isinstance(angles, dict) or DEFAULT_ANGLE not in angles:
        raise ValueError(f"angles must be a mapping with a '{DEFAULT_ANGLE}' angle")
    for name, angle in angles.items():
        if not isinstance(angle, dict):
            raise ValueError(f"angle {name} must be a mapping")
        if angle.get("page", "product") not in ANGLE_PAGES:
            raise ValueError(f"angle {name}: page must be one of {', '.join(ANGLE_PAGES)}")
        if "image" in angle and image_file(str(angle["image"])) is None:
            raise ValueError(f"angle {name}: no static/email/{angle['image']} image")
        match = angle.get("match", [])
        if not isinstance(match, list) or not all(isinstance(m, str) and m.strip() for m in match):
            raise ValueError(f"angle {name}: match must be a list of words")
        for f in ANGLE_FIELDS:
            for i, text in enumerate(_alternatives(f"angles.{name}.{f}", angle.get(f))):
                _check_text(f"angles.{name}.{f}[{i}]", text, False)
                if set(_fields(text)) & (set(ANGLE_FIELDS) | {"answer", "follow_up_when"}):
                    raise ValueError(f"angles.{name}.{f} cannot use angle or reply placeholders")


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
            _check_any(f"steps.{step}.{v}.subject", block[v].get("subject"), False)
            _check_any(f"steps.{step}.{v}.body", block[v].get("body"), True)
            rich = block[v].get("rich")
            if rich is not None:
                validate_rich(f"steps.{step}.{v}", rich)
            elif any(m in t for t in _alternatives("", block[v].get("body")) for m in MARKERS):
                raise ValueError(f"steps.{step}.{v}: [[image]] / [[button]] need a rich: block")
            used = {f for t in _alternatives("", block[v].get("body")) + _alternatives("", block[v].get("subject"))
                    for f in _fields(t)}
            if used & set(ANGLE_FIELDS) and "angles" not in data:
                raise ValueError(f"steps.{step}.{v} uses angle placeholders but there are no angles")
    for key in REPLY_KEYS:
        if key not in data["replies"]:
            raise ValueError(f"missing reply {key}")
        _check_any(f"replies.{key}.body", data["replies"][key].get("body"), True)
    for key in COURTESY_REPLY_KEYS:  # optional: without one, that reply simply isn't sent
        if key in data["replies"]:
            _check_any(f"replies.{key}.body", data["replies"][key].get("body"), False)
    _validate_angles(data)
    return data


def load_templates(path: Optional[str] = None) -> Dict[str, Any]:
    p = str(Path(path) if path else DEFAULT_TEMPLATES)
    if p not in _cache:
        with open(p, encoding="utf-8") as fh:
            _cache[p] = _validate(yaml.safe_load(fh))
    return _cache[p]


def _clean(value: Optional[str]) -> str:
    return _WS.sub(" ", value or "").strip()[:MAX_VALUE_LEN].strip()


_LEAD_URL = re.compile(r"(?:https?://|www\.)\S*", re.IGNORECASE)


def _lead_text(value: Optional[str]) -> str:
    """A lead-supplied value (name, company): no web addresses, so imported data can never put a
    link into our email."""
    return _clean(_LEAD_URL.sub("", value or ""))


def _pick(options: Any, lead: Lead, *salt: object) -> str:
    """One of the alternatives, the same for this lead every time (stable across restarts), but
    different across leads: no two prospects at a bank get the same email word for word."""
    items = options if isinstance(options, list) else [options]
    key = ":".join(str(x) for x in (lead.id, *salt)).encode()
    return items[int(hashlib.sha256(key).hexdigest()[:8], 16) % len(items)]


def angle_for(lead: Lead, templates: Dict[str, Any]) -> str:
    """The lead's angle: the first angle whose match words appear in its role (then industry)."""
    angles = templates.get("angles") or {}
    for text in (lead.role or "", getattr(lead, "industry", None) or ""):
        words = text.lower()
        for name, angle in angles.items():
            if any(re.search(rf"\b{re.escape(m.lower())}", words) for m in angle.get("match", [])):
                return name
    return DEFAULT_ANGLE


def _values(lead: Lead, step: int, settings: Settings,
            templates: Optional[Dict[str, Any]] = None, preview: bool = False) -> Dict[str, str]:
    def link(tag: str) -> str:
        if preview:  # the web app's preview: the real pages, no tracked link, no event
            targets = settings.link_targets
            return targets.get(tag) or targets.get("homepage") or "https://ozoo.ai/"
        return make_link(lead.id, step, tag, "email", settings=settings)

    name = _lead_text(lead.name)
    vals = {
        "name": name,
        "role": _lead_text(lead.role),
        "first_name": name.split(" ")[0] if name else "",
        "company": _lead_text(lead.company),
        "booking_link": link("booking"),
        "unsubscribe_link": link("unsubscribe"),
        "pricing_link": link("pricing"),
        "case_study_link": link("case_study"),
        "product_link": link("product"),
        "voice_link": link("voice"),
        "chat_link": link("chat"),
        "trial_link": link("trial"),
        "homepage_link": link("homepage"),
        "signature": getattr(settings, "email_signature", "") or "The Ozoo.ai team",
        "answer": "",
        "follow_up_when": "in a few months",
    }
    angles = (templates or {}).get("angles") or {}
    if angles:
        name_ = angle_for(lead, templates or {})
        angle = angles[name_]
        vals["angle_link"] = vals[f"{angle.get('page', 'product')}_link"]
        for f in ANGLE_FIELDS:
            vals[f] = _pick(angle[f], lead, step, name_, f).format_map(vals)
    return vals


def _variant(lead: Lead) -> str:
    present = (lead.variant_flags or {}).get("variants", [])
    return next((v for v in VARIANT_PRIORITY if v in present), "standard")


# Versions (task 22): every body alternative of a step variant is one built-in version; the web app
# (template_library.py) adds approved custom versions under CUSTOM_KEY and switched-off built-in
# keys under OFF_KEY of a copy of the templates dict.
CUSTOM_KEY = "_custom"
OFF_KEY = "_off"


def builtin_key(step: int, variant: str, index: int) -> str:
    return f"builtin.s{step}.{variant}.{index}"


def step_versions(t: Dict[str, Any], step: int, variant: str) -> List[Dict[str, Any]]:
    """The built-in versions of one step variant (one per body alternative)."""
    block = t["steps"][step][variant]
    return [{"key": builtin_key(step, variant, i), "kind": "sequence", "step": step, "variant": variant,
             "angle": "any", "subject": block["subject"], "body": b, "rich": block.get("rich")}
            for i, b in enumerate(_alternatives("", block["body"]))]


def live_versions(t: Dict[str, Any], step: int, variant: str, angle: str) -> List[Dict[str, Any]]:
    off = set(t.get(OFF_KEY) or ())
    custom = [c for c in t.get(CUSTOM_KEY) or () if c.get("kind") == "sequence" and c.get("step") == step
              and c.get("variant") == variant and c.get("angle") in ("any", angle)]
    return [v for v in step_versions(t, step, variant) if v["key"] not in off] + custom


def render_version(lead: Lead, version: Dict[str, Any], step: int, *, templates: Dict[str, Any],
                   settings: Settings, preview: bool = False) -> Dict[str, str]:
    """One version for one lead: {"subject", "text", "html"} ("html" empty for a plain email)."""
    vals = _values(lead, step, settings, templates, preview=preview)
    salt = (step, version.get("variant", ""), version.get("key", ""))
    subject = _clean(_pick(version["subject"], lead, *salt, "subject").format_map(vals))  # one line
    subject = subject[:1].upper() + subject[1:]  # "{topic}" subjects start lower-case
    body = _pick(version["body"], lead, *salt, "body").format_map(vals)
    rich = dict(version["rich"]) if version.get("rich") else None
    if rich and rich.get("image") == "angle":  # the screen that fits the lead's angle
        angle = (templates.get("angles") or {}).get(angle_for(lead, templates), {})
        rich["image"] = angle.get("image", "call-analysis")
        rich.setdefault("image_link", angle.get("page", "product"))
    parts = rich_parts(body, rich, vals, settings.link_base_url)
    return {"subject": subject, "text": parts["text"], "html": parts["html"]}


def choose_version(lead: Lead, step: int, templates: Dict[str, Any],
                   variant: Optional[str] = None) -> Dict[str, Any]:
    """The lead's version of `step`: its variant's live versions (`variant` = the kind a send plan
    chose, else from what the lead did), else the standard ones, else (all switched off) the
    built-in standard ones: an email is never blank."""
    v = variant if variant in STEP_VARIANTS else _variant(lead)
    angle = angle_for(lead, templates)
    cands = (live_versions(templates, step, v, angle) or live_versions(templates, step, "standard", angle)
             or step_versions(templates, step, "standard"))
    return _pick(cands, lead, step, v, "version")


def render_step(
    lead: Lead, step: int, *, templates: Optional[Dict[str, Any]] = None, settings: Optional[Settings] = None,
    variant: Optional[str] = None,
) -> Dict[str, str]:
    t = templates or load_templates()
    s = settings or get_settings()
    out = render_version(lead, choose_version(lead, step, t, variant), step, templates=t, settings=s)
    rendered = {f"subject_{step}": out["subject"], f"body_{step}": out["text"]}
    if out["html"]:
        rendered[f"html_{step}"] = out["html"]  # the designed version (task 21); the text always goes too
    return rendered


def has_reply(key: str, templates: Optional[Dict[str, Any]] = None) -> bool:
    return key in (templates or load_templates())["replies"]


def render_reply(
    lead: Lead, key: str, *, templates: Optional[Dict[str, Any]] = None, settings: Optional[Settings] = None,
    values: Optional[Dict[str, str]] = None,
) -> str:
    """Reply `key`; `values` fills answer / follow_up_when (plain text, not a template)."""
    t = templates or load_templates()
    s = settings or get_settings()
    vals = _values(lead, lead.step_sent, s, t)
    for k, v in (values or {}).items():
        if k in ("answer", "follow_up_when"):
            vals[k] = str(v)
    body = _pick(t["replies"][key]["body"], lead, "reply", key).format_map(vals)
    return body


def _tracking_vars(lead: Lead, campaign_step: int, real: int, s: Settings) -> Dict[str, str]:
    """Per-step values for tools that send the emails themselves (mailpit / gmail): the
    List-Unsubscribe URL and, unless EMAIL_OPEN_PIXEL=false, the open-tracking image. Both carry our
    step (real), keyed by the campaign step. Instantly ignores them."""
    out = {f"unsubscribe_url_{campaign_step}": make_unsubscribe_url(lead.id, real, settings=s)}
    if getattr(s, "email_open_pixel", False):
        out[f"open_pixel_{campaign_step}"] = make_open_pixel(lead.id, real, settings=s)
    if getattr(s, "email_honeypot", True):
        out[f"trap_url_{campaign_step}"] = make_trap_url(lead.id, real, settings=s)
    return out


def _live_templates(session: Session) -> Dict[str, Any]:
    """The YAML plus the web app's approved / switched-off versions (template_library, task 22)."""
    from .template_library import templates_for  # imports this module
    return templates_for(session)


LAST_STEP = max(SCHEDULE_DAYS)
STEP_OFFSET = "step_offset"
# Send plans (task 26, send_plan.py): the lead's plan id, the plan emails decided so far (node ids,
# one per our step) and the decided-but-not-sent email; PLAN_VAR tells the tool to wait for due_at.
PLAN_FLAG = "plan_id"
PATH_FLAG = "plan_path"
SCHED_FLAG = "plan_next_sched"
PLAN_VAR = "plan"


def _plan_id(session: Session, lead: Lead, tool: Any) -> Optional[int]:
    """The send plan for this enrolment, or None (the fixed 0 / 4 / 9 / 15 day schedule): only a
    tool that sends the steps itself on our dates (SUPPORTS_SCHEDULE) can follow a plan."""
    if not getattr(tool, "SUPPORTS_SCHEDULE", False):
        return None
    from .send_plan import plan_for_lead  # imports this module
    plan = plan_for_lead(session, lead)
    return plan.id if plan is not None else None


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
    t = templates or _live_templates(session)
    offset = from_step - 1
    variables: Dict[str, str] = {}
    for step in SCHEDULE_DAYS:
        real = min(step + offset, LAST_STEP)
        rendered = render_step(lead, real, templates=t, settings=s)
        variables[f"subject_{step}"] = rendered[f"subject_{real}"]
        variables[f"body_{step}"] = rendered[f"body_{real}"]
        variables[f"html_{step}"] = rendered.get(f"html_{real}", "")  # the designed version, if any
        variables.update(_tracking_vars(lead, step, real, s))
    vals = _values(lead, 1, s)
    variables["first_name"] = vals["first_name"]
    variables["company"] = vals["company"]
    plan_id = _plan_id(session, lead, tool)
    if plan_id is not None:
        variables[PLAN_VAR] = "1"  # the tool sends a step only once the plan gives it a due_at_<step>
    lead.tool_lead_id = tool.add_lead(campaign_id, lead, variables)
    flags = dict(lead.variant_flags or {})
    if plan_id is not None:
        flags[PLAN_FLAG] = plan_id
        flags[PATH_FLAG] = list(flags.get(PATH_FLAG) or [])[:from_step - 1]  # re-decided from here
        flags.pop(SCHED_FLAG, None)
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
    if (lead.variant_flags or {}).get(PLAN_FLAG) is not None:
        return  # a send plan renders each email when it decides it (send_plan.commit_next)
    real = lead.step_sent + 1
    campaign_step = real - step_offset(lead)
    if campaign_step < 1:
        return
    rendered = render_step(lead, real, templates=templates or _live_templates(session), settings=settings)
    tool.update_variables(lead.tool_lead_id, {f"subject_{campaign_step}": rendered[f"subject_{real}"],
                                              f"body_{campaign_step}": rendered[f"body_{real}"],
                                              f"html_{campaign_step}": rendered.get(f"html_{real}", ""),
                                              **_tracking_vars(lead, campaign_step, real,
                                                               settings or get_settings())})


def end_of_sequence_route(lead: Lead, human_clicks: int, *,
                          clicks_need_check: bool = False) -> Tuple[str, List[Action]]:
    """After Email 4 + 5 days with no reply. Opens are not used (unreliable), and there is
    no WhatsApp step here: opted-in leads already got WhatsApp on day 1. The tool lead is stopped
    first (its campaign has nothing left to send; a failed stop is retried by the worker).
    A hot score here comes from clicks and visits (no reply): with clicks_need_check and no verified
    page visit (a person read the page, task 19) it is a click_check alert."""
    if band(lead.score) == "hot":
        verified = bool((lead.variant_flags or {}).get("verified_visit"))
        alert = (Alert("click_check", "Finished the sequence without replying; hot score from "
                       "link clicks only, which may be a company security scanner. Check before contacting")
                 if clicks_need_check and not verified else
                 Alert("hot_no_reply", "Hot lead finished the sequence without replying"))
        return "sales_personal", [ToolStop(), SetState(LeadState.NURTURE, "end_hot"), alert]
    if human_clicks > 0:
        return "warm_2_3w", [ToolStop(), SetState(LeadState.NURTURE, "end_clicked")]
    return "monthly", [ToolStop(), SetState(LeadState.NURTURE, "end_silent")]
