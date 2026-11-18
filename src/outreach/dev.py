"""Local developer CLI (APP_ENV=local only). See docs/guides/local-testing.md.

  python -m outreach.dev setup [--force]          write .env.local (fresh secrets) + create local.db
  python -m outreach.dev seed                     5 sample leads through the real intake function
  python -m outreach.dev show [LEAD_ID] [--mask]  leads, or one lead's state / events / chat / jobs / alerts
  python -m outreach.dev tick                     run one worker tick now
  python -m outreach.dev advance --hours N        move the local fake clock forward (--reset: back to now)
  python -m outreach.dev sim SCENARIO --lead ID   send a signed webhook to the running local server

Settings come from .env.local (`--env-file`); variables already in the environment win. Every
command except setup refuses to run unless APP_ENV=local. `show` prints emails and phone numbers
on purpose (local data; --mask hides them); nothing here writes them to a log.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from . import devsim
from .config import Settings
from .db import init_db, make_engine
from .localclock import DEFAULT_CLOCK_FILE, LocalClock, clock_file
from .models import Alert as AlertRow
from .models import ChatMessage, Event, Job, Lead
from .score import band

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env.local"
DEFAULT_EXAMPLE = REPO_ROOT / ".env.example"
DEFAULT_DB = REPO_ROOT / "local.db"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
SHOW_ROWS = 15
TEXT_COL = 60
JOB_WAIT_S = 1.0  # the server runs classifier / bot jobs right after its response

SEED_LEADS: List[Dict[str, Any]] = [
    {"label": "email only", "name": "Ana Email", "company": "Acme Local", "role": "CTO",
     "email": "ana@acme.example.com", "country": "US"},
    {"label": "WhatsApp only (opted in)", "name": "Wes WhatsApp", "company": "Waco Local", "role": "COO",
     "phone": "+12015550101", "country": "US", "consent_whatsapp": True},
    {"label": "both channels (opted in)", "name": "Bo Both", "company": "Both Local", "role": "CEO",
     "email": "bo@both.example.com", "phone": "+12015550102", "country": "US", "consent_whatsapp": True},
    {"label": "EU, email only (held until EU_EMAIL_APPROVED)", "name": "Eva Euro", "company": "Euro GmbH",
     "role": "Head of Ops", "email": "eva@euro.example.com", "country": "DE"},
    {"label": "email that will bounce (sim email-bounce --hard)", "name": "Dan Nomail", "company": "Nomail Local",
     "role": "CFO", "email": "dan@nomailbox.example.com", "country": "US"},
]

SCENARIOS = ("email-sent", "email-reply", "email-bounce", "email-unsub", "email-open", "click", "unsub-link",
             "wa-button", "wa-text", "wa-status", *devsim.CAL_TRIGGERS, "teams")


class CliError(Exception):
    pass


# ====================================================================== setup

def _local_values(db: Path, clock: Optional[Path]) -> Dict[str, str]:
    targets = {"homepage": "https://example.com/", "booking": "https://example.com/book",
               "pricing": "https://example.com/pricing", "case_study": "https://example.com/case-study",
               "product": "https://example.com/product"}
    return {
        "APP_ENV": "local",
        "EMAIL_MODE": "fake", "WHATSAPP_MODE": "fake", "LLM_MODE": "fake", "ALERTS_MODE": "fake",
        "VERIFY_MODE": "fake",
        "DATABASE_URL": f"sqlite:///{db}",
        "LINK_SECRET": secrets.token_urlsafe(32),
        "LINK_BASE_URL": DEFAULT_BASE_URL,
        "LINK_TARGETS": "'" + json.dumps(targets) + "'",
        "TOOL_NAME": "instantly",
        "TOOL_WEBHOOK_SECRET": secrets.token_urlsafe(32),
        "CAMPAIGN_IDS": "'" + json.dumps({"default": "local-campaign"}) + "'",
        "DEFAULT_TZ_GROUP": "default",
        "INTAKE_API_KEY": secrets.token_urlsafe(32),
        "CAL_WEBHOOK_SECRET": secrets.token_urlsafe(32),
        "TEAMS_OUTGOING_SECRET": base64.b64encode(secrets.token_bytes(32)).decode(),
        "TEAMS_TENANT_ID": "local-tenant",
        "TEAMS_SALES_CHANNEL_ID": "19:local-sales@thread.tacv2",
        "TEAMS_ALLOWED_AAD_IDS": "local-salesperson",
        "WA_APP_SECRET": secrets.token_urlsafe(32),
        "WA_VERIFY_TOKEN": secrets.token_urlsafe(16),
        "RUN_WORKER": "1",
        "WORKER_INTERVAL_S": "10",
        "FAKE_CLOCK_FILE": str(clock) if clock else "",
    }


_KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=")


def render_env_local(example_text: str, values: Dict[str, str]) -> str:
    """.env.example with the local values filled in (comments kept); keys the example does not
    have are appended. Every other key is left commented out (unset: its default, or blank where
    fake mode needs no credential)."""
    out = ["# Local run settings, written by scripts/local_setup. Never commit this file.",
           "# Every service is fake (no real accounts, no network, no cost). See docs/guides/local-testing.md.", ""]
    done = set()
    for line in example_text.splitlines():
        m = _KEY_LINE.match(line)
        if m and m.group(1) in values:
            out.append(f"{m.group(1)}={values[m.group(1)]}")
            done.add(m.group(1))
        elif m and not line[m.end():].strip():
            out.append("# " + line)  # blank = use the default (a blank value would not validate)
        else:
            out.append(line)
    rest = [k for k in values if k not in done]
    if rest:
        out += ["", "# --- local run"] + [f"{k}={values[k]}" for k in rest]
    return "\n".join(out) + "\n"


def cmd_setup(args: argparse.Namespace) -> int:
    path = Path(args.env_file or DEFAULT_ENV_FILE)
    db = Path(args.db or DEFAULT_DB).resolve()
    if path.exists() and not args.force:
        print(f"{path} exists, kept (use --force to regenerate its secrets)")
    else:
        example = Path(args.example or DEFAULT_EXAMPLE).read_text(encoding="utf-8")
        clock = Path(args.clock_file).resolve() if args.clock_file else None
        text = render_env_local(example, _local_values(db, clock))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(path, 0o600)
        print(f"wrote {path} (fresh secrets, every service fake)")
    settings = _settings(str(path))
    engine = make_engine(settings.database_url)
    init_db(engine)
    engine.dispose()
    print(f"database ready: {settings.database_url}")
    return 0


# ====================================================================== shared

def _settings(env_file: Optional[str]) -> Settings:
    path = env_file or (str(DEFAULT_ENV_FILE) if DEFAULT_ENV_FILE.exists() else ".env")
    return Settings(_env_file=path if Path(path).exists() else None)


def _local_settings(env_file: Optional[str]) -> Settings:
    try:
        settings = _settings(env_file)
    except ValidationError as exc:
        fields = ", ".join(sorted({".".join(str(x) for x in e["loc"]) or e["msg"] for e in exc.errors()}))
        raise CliError(f"settings invalid ({fields}); run scripts/local_setup first") from None
    if settings.app_env != "local":
        raise CliError("refusing to run: the dev CLI needs APP_ENV=local")
    return settings


def _factory(settings: Settings) -> sessionmaker:
    engine = make_engine(settings.database_url)
    init_db(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _clock(settings: Settings) -> LocalClock:
    return LocalClock(clock_file(settings))


def mask_email(v: Optional[str]) -> str:
    if not v:
        return "-"
    user, _, domain = v.partition("@")
    return f"{user[:1]}***@{domain}"


def mask_phone(v: Optional[str]) -> str:
    if not v:
        return "-"
    return v[:5] + "*" * max(len(v) - 7, 0) + v[-2:]


def _short(text: Any, n: int = TEXT_COL) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def _table(headers: Sequence[str], rows: List[Sequence[Any]]) -> str:
    cells = [[str(c) for c in headers]] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _ts(dt: Any) -> str:
    return dt.strftime("%m-%d %H:%M:%S") if dt else "-"


def lead_summary(lead: Lead) -> str:
    flags = lead.variant_flags or {}
    shown = ", ".join(f"{k}={v}" if not isinstance(v, (dict, list)) else k for k, v in sorted(flags.items()))
    channel = (lead.active_channel or "-") + (" (switched)" if lead.channel_switched else "")
    extra = f" pause={lead.pause_reason}" if lead.pause_reason else ""
    extra += f" nurture_until={lead.nurture_until:%Y-%m-%d}" if lead.nurture_until else ""
    extra += f" human_owner={lead.wa_human_owner_name or lead.wa_human_owner}" if lead.wa_human_owner else ""
    return (f"lead {lead.id}: state={lead.state} channel={channel} score={lead.score} ({band(lead.score)}) "
            f"step_sent={lead.step_sent}{extra}\n  flags: {shown or '-'}")


# ====================================================================== seed

def cmd_seed(args: argparse.Namespace, settings: Settings) -> int:
    from .intake import intake_lead
    from .verify import make_verifier

    factory = _factory(settings)
    verifier = make_verifier(settings)
    now = _clock(settings)()
    rows = []
    with factory() as s:
        for spec in SEED_LEADS:
            payload = {k: v for k, v in spec.items() if k != "label"}
            payload["source"] = "local_seed"
            r = intake_lead(s, payload, verifier, now, primary_when_both=settings.primary_when_both,
                            eu_email_approved=settings.eu_email_approved)
            s.commit()
            rows.append((r.lead_id if r.lead_id is not None else "-", r.outcome, r.channel or "-", spec["label"]))
    print(_table(("lead", "outcome", "channel", "sample"), rows))
    print("next: `python -m outreach.dev tick` starts them (the running server also ticks every few seconds)")
    return 0


# ====================================================================== show

def _contact(v: Optional[str], mask: bool, fn: Callable[[Optional[str]], str]) -> str:
    return fn(v) if mask else (v or "-")


def cmd_show(args: argparse.Namespace, settings: Settings) -> int:
    factory = _factory(settings)
    print(f"fake clock: {_clock(settings)():%Y-%m-%d %H:%M:%S} UTC")
    with factory() as s:
        if args.lead_id is None:
            leads = list(s.scalars(select(Lead).order_by(Lead.id)))
            print(_table(("id", "state", "channel", "score", "name", "email", "phone"), [
                (ld.id, ld.state, (ld.active_channel or "-") + ("*" if ld.channel_switched else ""),
                 f"{ld.score} {band(ld.score)}", _short(f"{ld.name} @ {ld.company}", 30),
                 _contact(ld.email, args.mask, mask_email), _contact(ld.phone, args.mask, mask_phone))
                for ld in leads]) if leads else "no leads yet (python -m outreach.dev seed)")
            return 0
        lead = s.get(Lead, args.lead_id)
        if lead is None:
            raise CliError(f"lead {args.lead_id} not found")
        _show_lead(s, lead, args.mask)
    return 0


def _show_lead(s: Session, lead: Lead, mask: bool) -> None:
    print(lead_summary(lead))
    print(f"  {lead.name} @ {lead.company} ({lead.role}); email {_contact(lead.email, mask, mask_email)} "
          f"[{lead.verify_status or '-'}]; phone {_contact(lead.phone, mask, mask_phone)} "
          f"(WhatsApp consent: {'yes' if lead.consent_whatsapp else 'no'}); EU: {'yes' if lead.is_eu else 'no'}"
          f"{'; email hold ' + lead.email_hold if lead.email_hold else ''}")
    print(f"  email tool id {lead.tool_lead_id or '-'}; last sent {_ts(lead.last_sent_at)}; WhatsApp intro "
          f"{_ts(lead.wa_template_sent_at)}, reminder {_ts(lead.wa_reminder_sent_at)}, window until "
          f"{_ts(lead.wa_window_until)}")

    events = list(s.scalars(select(Event).where(Event.lead_id == lead.id)
                            .order_by(Event.id.desc()).limit(SHOW_ROWS)))[::-1]
    print("\nEvents (latest %d)" % SHOW_ROWS)
    print(_table(("id", "at", "channel", "kind", "tag", "step", "verdict", "done", "detail"), [
        (e.id, _ts(e.occurred_at), e.channel, e.kind, e.tag or "-", e.step if e.step is not None else "-",
         e.verdict or "-", "y" if e.processed else "n", _event_detail(e)) for e in events]) if events else "  none")

    chat = list(s.scalars(select(ChatMessage).where(ChatMessage.lead_id == lead.id)
                          .order_by(ChatMessage.id.desc()).limit(SHOW_ROWS)))[::-1]
    print("\nChat messages (latest %d)" % SHOW_ROWS)
    print(_table(("id", "at", "dir", "author", "text"), [
        (m.id, _ts(m.created_at), m.direction, m.author, _short(m.text)) for m in chat]) if chat else "  none")

    jobs = list(s.scalars(select(Job).where(Job.lead_id == lead.id).order_by(Job.id.desc()).limit(SHOW_ROWS)))[::-1]
    print("\nJobs")
    print(_table(("id", "kind", "status", "attempts", "last_error"), [
        (j.id, j.kind, j.status, j.attempts, j.last_error or "-") for j in jobs]) if jobs else "  none")

    alerts = list(s.scalars(select(AlertRow).where(AlertRow.lead_id == lead.id)
                            .order_by(AlertRow.id.desc()).limit(SHOW_ROWS)))[::-1]
    print("\nAlerts")
    print(_table(("id", "at", "kind", "text"), [
        (a.id, _ts(a.created_at), a.kind, _short(a.text)) for a in alerts]) if alerts else "  none")


def _event_detail(e: Event) -> str:
    d = e.data or {}
    parts = []
    if e.kind == "state_change":
        return f"{d.get('from')} -> {d.get('to')} ({d.get('reason')})"
    if d.get("classification"):
        parts.append(f"class={d['classification'].get('label')}")
    for key in ("text", "payload", "code", "fit", "uid"):
        if d.get(key) not in (None, ""):
            parts.append(f"{key}={_short(d[key], 40)}")
    return " ".join(parts) or "-"


# ====================================================================== tick / advance

def cmd_tick(args: argparse.Namespace, settings: Settings) -> int:
    from . import worker
    from .app import default_deps, wire_deps
    from .logging_setup import configure as _local_logging
    from .verify import make_verifier

    _local_logging()
    deps = wire_deps(default_deps(settings), settings, make_verifier(settings))
    result = worker.tick(_factory(settings), deps, _clock(settings), settings)
    busy = {k: v for k, v in result.items() if v}
    print("tick: " + (", ".join(f"{k}={v}" for k, v in busy.items()) if busy else "nothing to do"))
    return 0


def cmd_advance(args: argparse.Namespace, settings: Settings) -> int:
    clock = _clock(settings)
    if args.reset:
        now = clock.reset()
    elif args.hours is None:
        raise CliError("advance needs --hours N or --reset")
    else:
        now = clock.advance(timedelta(hours=args.hours))
    off = clock.offset().total_seconds() / 3600
    print(f"fake clock now {now:%Y-%m-%d %H:%M:%S} UTC ({off:+.1f} h from real time). "
          "Run `python -m outreach.dev tick` (or wait for the server's worker) to act on it.")
    return 0


# ====================================================================== sim

def _build(args: argparse.Namespace, settings: Settings, lead: Optional[devsim.SimLead], now: Any,
           s: Session) -> devsim.SimRequest:
    sc = args.scenario
    if sc == "teams":
        if not args.text:
            raise CliError('teams needs --text, e.g. --text "wa 2 hello" or --text "done 2"')
        return devsim.teams_command(settings, now, text=args.text)
    if lead is None:
        raise CliError(f"{sc} needs --lead ID")
    if sc == "email-sent":
        return devsim.email_sent(settings, lead, now, step=args.step or 1, minutes_ago=args.minutes_ago)
    if sc == "email-reply":
        if not args.text:
            raise CliError("email-reply needs --text")
        return devsim.email_reply(settings, lead, now, text=args.text, step=args.step)
    if sc == "email-bounce":
        return devsim.email_bounce(settings, lead, now, hard=not args.soft)
    if sc == "email-unsub":
        return devsim.email_unsub(settings, lead, now)
    if sc == "email-open":
        return devsim.email_open(settings, lead, now, ip=args.ip or "203.0.113.7")
    if sc == "click":
        return devsim.click(settings, lead, tag=args.tag or "homepage", step=args.step, channel=args.channel,
                            scanner=args.scanner)
    if sc == "unsub-link":
        return devsim.unsubscribe_link(settings, lead, step=args.step, channel=args.channel or "email")
    if sc == "wa-button":
        return devsim.wa_button(settings, lead, now, button=args.id or "book_demo")
    if sc == "wa-text":
        if not args.text:
            raise CliError("wa-text needs --text")
        return devsim.wa_text(settings, lead, now, text=args.text)
    if sc == "wa-status":
        wamid = args.wamid or s.scalar(
            select(ChatMessage.wa_message_id).where(ChatMessage.lead_id == lead.id, ChatMessage.direction == "out",
                                                   ChatMessage.wa_message_id.is_not(None))
            .order_by(ChatMessage.id.desc()).limit(1))
        return devsim.wa_status(settings, lead, now, status=args.status or "delivered", code=args.code, wamid=wamid)
    return devsim.cal_event(settings, lead, now, sc, uid=args.uid, size=args.size, timeline=args.timeline)


def _send(req: devsim.SimRequest, base_url: str, client: Any = None) -> Any:
    if client is not None:  # tests: a FastAPI TestClient
        return client.request(req.method, req.path, headers=req.headers, content=req.body)
    import httpx

    with httpx.Client(timeout=10.0, follow_redirects=False) as http:
        return http.request(req.method, base_url.rstrip("/") + req.path, headers=req.headers, content=req.body)


def cmd_sim(args: argparse.Namespace, settings: Settings, client: Any = None) -> int:
    factory = _factory(settings)
    now = _clock(settings)()
    lead_id = args.lead
    if lead_id is None and args.scenario == "teams" and args.text:
        m = re.search(r"\b(\d{1,18})\b", args.text)
        lead_id = int(m.group(1)) if m else None
    with factory() as s:
        row = s.get(Lead, lead_id) if lead_id is not None else None
        if lead_id is not None and row is None and args.scenario != "teams":
            raise CliError(f"lead {lead_id} not found")
        try:
            req = _build(args, settings, devsim.SimLead.of(row) if row is not None else None, now, s)
        except ValueError as exc:
            raise CliError(str(exc)) from None
    base = args.base_url or os.environ.get("DEV_BASE_URL") or DEFAULT_BASE_URL
    try:
        resp = _send(req, base, client)
    except Exception as exc:  # noqa: BLE001 - e.g. the server is not running
        raise CliError(f"could not reach {base} ({type(exc).__name__}); is scripts/run_local running?") from None
    detail = ""
    if resp.status_code in (301, 302, 303, 307):
        detail = f" -> {resp.headers.get('location')}"
    elif args.scenario == "click" and resp.status_code == 200:
        # our click page: a person's browser runs its script (the beacon); --scanner never does
        beacon = None if getattr(args, "scanner", False) else devsim.click_confirm(resp.text)
        target = devsim.click_target(resp.text)
        if beacon is not None:
            try:
                _send(beacon, base, client)
                detail = f" -> {target} (browser confirmed the click)"
            except Exception as exc:  # noqa: BLE001
                detail = f" -> {target} (beacon failed: {type(exc).__name__})"
        elif target:
            detail = f" -> {target} (no browser script ran)"
    elif args.scenario == "teams":
        try:
            detail = f" teams reply: {resp.json().get('text')}"
        except ValueError:
            pass
    print(f"{req.method} {req.path[:60]}  HTTP {resp.status_code}{detail}")
    if row is not None:
        if client is None:
            time.sleep(JOB_WAIT_S)  # let the server finish the classifier / bot job it started
        with factory() as s:
            print(lead_summary(s.get(Lead, row.id)))
    return 0 if resp.status_code < 400 else 1


# ====================================================================== main

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m outreach.dev", description="Local testing helpers (APP_ENV=local).")
    p.add_argument("--env-file", help=f"settings file (default {DEFAULT_ENV_FILE})")
    sub = p.add_subparsers(dest="cmd", required=True)

    st = sub.add_parser("setup", help="write .env.local with fresh secrets and create the local database")
    st.add_argument("--force", action="store_true", help="overwrite an existing .env.local")
    st.add_argument("--env-file", dest="env_file_sub")
    st.add_argument("--example", help=argparse.SUPPRESS)
    st.add_argument("--db", help=f"SQLite file (default {DEFAULT_DB})")
    st.add_argument("--clock-file", help=f"fake clock file (default {DEFAULT_CLOCK_FILE})")

    sub.add_parser("seed", help="add 5 sample leads through the real intake")
    sh = sub.add_parser("show", help="list leads, or show one lead in detail")
    sh.add_argument("lead_id", nargs="?", type=int)
    sh.add_argument("--mask", action="store_true", help="hide emails and phone numbers")
    sub.add_parser("tick", help="run one worker tick now")
    ad = sub.add_parser("advance", help="move the local fake clock")
    ad.add_argument("--hours", type=float)
    ad.add_argument("--reset", action="store_true", help="back to real time")

    sm = sub.add_parser("sim", help="send a signed webhook to the running local server")
    sm.add_argument("scenario", choices=SCENARIOS)
    sm.add_argument("--lead", type=int)
    sm.add_argument("--text")
    sm.add_argument("--step", type=int)
    sm.add_argument("--minutes-ago", type=float, default=10.0, help="email-sent: when it went out (default 10)")
    bounce = sm.add_mutually_exclusive_group()
    bounce.add_argument("--hard", action="store_true", help="email-bounce: hard (the default)")
    bounce.add_argument("--soft", action="store_true", help="email-bounce: soft (3 soft = hard)")
    sm.add_argument("--ip", help="email-open: client IP (17.x.x.x = Apple Mail privacy proxy)")
    sm.add_argument("--tag", choices=sorted(devsim_tags()), help="click: link purpose")
    sm.add_argument("--channel", choices=("email", "whatsapp"), help="click: channel in the link")
    sm.add_argument("--scanner", action="store_true", help="click: a security scanner's user agent")
    sm.add_argument("--id", choices=sorted(devsim.WA_BUTTONS), help="wa-button: which button")
    sm.add_argument("--status", choices=("sent", "delivered", "read", "failed"), help="wa-status")
    sm.add_argument("--code", type=int, help="wa-status failed: Meta error code (131026 = not on WhatsApp)")
    sm.add_argument("--wamid", help="wa-status: message id (default: the lead's last outbound message)")
    sm.add_argument("--uid", help="cal-*: booking uid (default sim-<lead id>)")
    sm.add_argument("--size", help="cal-form: company size answer, e.g. 51-200")
    sm.add_argument("--timeline", help="cal-form: timeline answer, e.g. now, 3 months")
    sm.add_argument("--base-url", help=f"server (default $DEV_BASE_URL or {DEFAULT_BASE_URL})")
    return p


def devsim_tags() -> List[str]:
    from .links import TRAP_TAG, VALID_TAGS

    return sorted(VALID_TAGS - {TRAP_TAG})


def main(argv: Optional[List[str]] = None, *, client: Any = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.cmd == "setup":
            args.env_file = args.env_file_sub or args.env_file
            return cmd_setup(args)
        settings = _local_settings(args.env_file)
        if args.cmd == "seed":
            return cmd_seed(args, settings)
        if args.cmd == "show":
            return cmd_show(args, settings)
        if args.cmd == "tick":
            return cmd_tick(args, settings)
        if args.cmd == "advance":
            return cmd_advance(args, settings)
        return cmd_sim(args, settings, client)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2 if "refusing" in str(exc) or "settings invalid" in str(exc) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
