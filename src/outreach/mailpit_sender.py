"""Local sender for EMAIL_MODE=mailpit: `python -m outreach.mailpit_sender [--once]`.

Every few seconds it sends the sequence steps that are due (Email 1 right after enrolment, then
days 4 / 9 / 15 on the local clock, divided by TIME_SCALE) to Mailpit, then tells the running app
with the same signed `email_sent` webhook Instantly would send. Local only (APP_ENV=local).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Any, Callable, List, Optional

from . import devsim
from .emailtool.mailpit import DEFAULT_SMTP_HOST, DEFAULT_SMTP_PORT, MailpitEmailTool
from .localclock import LocalClock, clock_file
from .sequencer import SCHEDULE_DAYS

logger = logging.getLogger("outreach.mailpit_sender")

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_INTERVAL_S = 5.0


def make_tool(settings: Any, now: Optional[Callable[[], Any]] = None, **kw: Any) -> MailpitEmailTool:
    return MailpitEmailTool(
        settings.tool_webhook_secret,
        state_file=getattr(settings, "mailpit_state_file", None) or None,
        smtp_host=getattr(settings, "smtp_host", None) or DEFAULT_SMTP_HOST,
        smtp_port=getattr(settings, "smtp_port", None) or DEFAULT_SMTP_PORT,
        now=now or LocalClock(clock_file(settings)),
        **kw,
    )


def _post(req: devsim.SimRequest, base_url: str, client: Any = None) -> int:
    if client is not None:  # tests: FastAPI TestClient
        return client.request(req.method, req.path, headers=req.headers, content=req.body).status_code
    import httpx

    with httpx.Client(timeout=10.0) as http:
        return http.request(req.method, base_url.rstrip("/") + req.path, headers=req.headers,
                            content=req.body).status_code


def notify(tool: MailpitEmailTool, settings: Any, base_url: str, client: Any = None) -> int:
    """Report every sent-but-unreported step to the app. Returns how many were accepted."""
    n = 0
    for row in tool.unnotified():
        lead = devsim.SimLead(id=row["lead_id"] or 0, email=row["email"])
        for step in range(row["notified_step"] + 1, row["last_step"] + 1):
            req = devsim.email_event(settings, lead, tool._now(), "email_sent", step=step,
                                     email_subject=f"Step {step}")
            try:
                status = _post(req, base_url, client)
            except Exception as exc:  # noqa: BLE001 - app not up yet: retried on the next pass
                logger.warning("could not reach the app (%s); will retry", type(exc).__name__)
                return n
            if status >= 300:
                logger.warning("app answered HTTP %s for %s step %s; will retry", status, row["tool_lead_id"], step)
                return n
            tool.mark_notified(row["tool_lead_id"], step)
            n += 1
    return n


def run_once(tool: MailpitEmailTool, settings: Any, base_url: str, client: Any = None) -> List[dict]:
    try:
        sent = tool.send_due(SCHEDULE_DAYS, float(getattr(settings, "time_scale", 1.0) or 1.0))
    except (OSError, ConnectionError) as exc:
        logger.warning("Mailpit not reachable on %s:%s (%s). Is it running?",
                       tool.smtp_host, tool.smtp_port, type(exc).__name__)
        sent = []
    for s in sent:
        print(f"[MAILPIT] lead {s['lead_id']}: Email {s['step']} sent -> open http://localhost:8025", flush=True)
    notify(tool, settings, base_url, client)
    return sent


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m outreach.mailpit_sender", description=__doc__)
    p.add_argument("--env-file", help="settings file (default .env.local)")
    p.add_argument("--base-url", default=os.environ.get("DEV_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    p.add_argument("--once", action="store_true", help="one pass, then exit")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .dev import CliError, _local_settings

    try:
        settings = _local_settings(args.env_file)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if getattr(settings, "email_mode", None) != "mailpit":
        print("error: EMAIL_MODE is not mailpit in your settings; nothing to send", file=sys.stderr)
        return 2
    tool = make_tool(settings)
    print(f"mailpit sender: SMTP {tool.smtp_host}:{tool.smtp_port}, app {args.base_url}. Ctrl+C to stop.", flush=True)
    if args.once:
        run_once(tool, settings, args.base_url)
        return 0
    try:
        while True:
            run_once(tool, settings, args.base_url)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
