"""Run simulated leads through the real agent: intake, the real worker, our own sender (with its send
window), the tracked-link / open / visit / unsubscribe endpoints and the webhooks, all in-process
on a private temporary database. Nothing leaves the machine: SMTP is a recording stand-in, every
provider is the local fake, the clock is simulated.

Each delivered email triggers the persona's acts for that email plus its inbox's machines, at the
times they would happen. The report (report.py) then compares what the agent concluded with what
the person really did.
"""
from __future__ import annotations

import base64
import contextlib
import io
import heapq
import itertools
import logging
import random
import re
import secrets
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from .. import devsim, worker
from ..alerts import ConsoleAlerter
from ..app import create_app, default_deps
from ..config import Settings
from ..db import make_engine
from ..hours import parse_business_hours
from ..links import TRAP_TAG, VISIT_PARAM, make_link, make_open_pixel, parse_token, visit_token
from ..mailpit_sender import run_once
from ..models import Lead
from ..sendwindow import SendWindow
from ..sequencer import LAST_STEP, SCHEDULE_DAYS
from .personas import EVERY, MIX, STORY, Act, Persona

logger = logging.getLogger(__name__)

START = datetime(2026, 11, 16, 4, 30, tzinfo=timezone.utc)  # Monday 10:00 in India
IDLE_STEP = timedelta(hours=6)  # the worker's time-driven jobs (end of sequence, resume...) at most this late
DAYS = 50  # 4 emails over 15 days, 5 days' wait, an out-of-office return and a booking
SITE = "https://ozoo.example"
_LINK = re.compile(r"https?://[^\s\"'<>]+/(?:r|o)/[A-Za-z0-9_.\-]+")


class SimClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


class _Outbox:
    """Stand-in SMTP server: keeps every message, sends nothing."""

    def __init__(self) -> None:
        self.messages: List[Any] = []

    def __call__(self, host: str, port: int) -> "_Outbox":
        return self

    def send_message(self, msg: Any) -> None:
        self.messages.append(msg)

    def quit(self) -> None:
        pass


@dataclass
class SimLead:
    persona: Persona
    lead_id: int
    email: str
    colleague: str
    log: List[str] = field(default_factory=list)  # what happened, in order (the person and the machines)
    person_clicks: int = 0
    machine_clicks: int = 0
    targets: Dict[str, str] = field(default_factory=dict)  # tag -> page the last click forwarded to
    done: set = field(default_factory=set)  # the persona's acts already done (a person acts once)


def _settings(workdir: Path) -> Settings:
    targets = {"homepage": f"{SITE}/", "booking": "https://cal.example/ozoo/demo", "pricing": f"{SITE}/pricing",
               "case_study": f"{SITE}/case-study", "product": f"{SITE}/product"}
    return Settings(
        _env_file=None, app_env="local", email_mode="mailpit", whatsapp_mode="fake", llm_mode="fake",
        alerts_mode="fake", verify_mode="fake", database_url=f"sqlite:///{workdir / 'world.db'}",
        link_secret=secrets.token_urlsafe(32), link_base_url="https://track.ozoo.example", link_targets=targets,
        tool_name="instantly", tool_webhook_secret=secrets.token_urlsafe(24),
        campaign_ids={"default": "world"}, default_tz_group="default", intake_api_key=secrets.token_urlsafe(32),
        cal_webhook_secret=secrets.token_urlsafe(24), cal_booking_url=targets["booking"],
        teams_outgoing_secret=base64.b64encode(secrets.token_bytes(32)).decode(), teams_tenant_id="world",
        teams_sales_channel_id="19:world@thread.tacv2", teams_allowed_aad_ids="world-sales",
        wa_app_secret=secrets.token_urlsafe(24), wa_verify_token=secrets.token_urlsafe(12),
        mailpit_state_file=str(workdir / "outbox.db"), fake_clock_file=str(workdir / "clock.json"),
        trust_proxy_headers=True, email_open_pixel=True, run_worker=False,
    )


def pick_personas(n: Optional[int], seed: int) -> List[Persona]:
    """n=None: each story persona once. Else n leads drawn from the realistic MIX, their timings
    jittered (people are not punctual)."""
    if n is None:
        return list(STORY)
    rng = random.Random(seed)
    keys, weights = zip(*MIX)
    by_key = {p.key: p for p in STORY}
    out = []
    for _ in range(n):
        p = by_key[rng.choices(keys, weights)[0]]
        scale = rng.uniform(0.5, 1.8)
        acts = tuple(a if a.kind == "scan" else replace(a, after=a.after * scale) for a in p.acts)
        out.append(replace(p, acts=acts))
    return out


class World:
    def __init__(self, workdir: Path, personas: List[Persona]) -> None:
        workdir.mkdir(parents=True, exist_ok=True)
        self.settings = _settings(workdir)
        self.clock = SimClock(START)
        self.deps = default_deps(self.settings)
        self.alert_lines: List[str] = []
        self.deps.alerter = ConsoleAlerter(out=self.alert_lines.append)
        self.outbox = _Outbox()
        tool = self.deps.tool
        tool._now = self.clock  # one clock for the app, the worker and the sender
        tool._smtp_factory = self.outbox  # never a real SMTP connection
        tool.send_window = SendWindow(parse_business_hours("Mon-Fri 09:30-17:30"), ZoneInfo("Asia/Kolkata"))
        self.tool = tool
        self.engine = make_engine(self.settings.database_url)
        self.app = create_app(self.settings, deps=self.deps, engine=self.engine, clock=self.clock)
        self.client = TestClient(self.app, raise_server_exceptions=False, follow_redirects=False)
        logging.getLogger("outreach").setLevel(logging.WARNING)  # thousands of ticks: keep the console readable
        self.factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self._seq = itertools.count()
        self.sends: List[Dict[str, Any]] = []  # every sequence email the sender sent
        self._queue: List[Tuple[datetime, int, SimLead, Act, int, Dict[str, str]]] = []
        self.leads: List[SimLead] = [self._intake(i, p) for i, p in enumerate(personas, 1)]
        self._by_id = {s.lead_id: s for s in self.leads}

    # ------------------------------------------------------------ setup
    def _intake(self, i: int, p: Persona) -> SimLead:
        domain = f"lender{i}.sim-bank.example"  # never the persona key: the fake verifier reacts to words
        email = f"lead{i}@{domain}"
        body = {"name": f"Lead {i} Sim", "company": f"Sim Lender {i}", "role": "Head of Collections",
                "source": "world-sim", "email": email, "country": "IN"}
        r = self.client.post("/intake", json=body, headers={"X-API-Key": self.settings.intake_api_key})
        if r.status_code not in (200, 201) or r.json().get("lead_id") is None:
            raise RuntimeError(f"intake refused simulated lead {i}: HTTP {r.status_code}")
        return SimLead(p, r.json()["lead_id"], email, colleague=f"colleague{i}@{domain}")

    # ------------------------------------------------------------ the loop
    def run(self, days: int = DAYS) -> "World":
        end = START + timedelta(days=days)
        while self.clock.now < end:
            self._send_due()
            self._run_due_acts()
            self._tick()
            nxt = min(self._queue[0][0] if self._queue else end, self._next_send() or end,
                      self.clock.now + IDLE_STEP, end)
            self.clock.now = max(nxt, self.clock.now + timedelta(seconds=1))
        self._tick()
        return self

    def _next_send(self) -> Optional[datetime]:
        """When the sender has its next email due (it sends at its send-window slot)."""
        soonest: Optional[datetime] = None
        with self.tool._db() as db:
            rows = db.execute("SELECT tool_lead_id, last_step, enrolled_at FROM mp_lead "
                              "WHERE status = 'active' AND last_step < ?", (LAST_STEP,)).fetchall()
        for row in rows:
            due = self.tool.due_at(datetime.fromisoformat(row["enrolled_at"]), row["last_step"] + 1,
                                   SCHEDULE_DAYS, 1.0, key=row["tool_lead_id"])
            soonest = due if soonest is None or due < soonest else soonest
        return max(soonest, self.clock.now + timedelta(seconds=1)) if soonest else None

    def _tick(self) -> None:
        worker.tick(self.factory, self.deps, self.clock, self.settings)

    def _send_due(self) -> None:
        before = len(self.outbox.messages)
        with contextlib.redirect_stdout(io.StringIO()):  # the sender's console lines
            sent = run_once(self.tool, self.settings, "", client=self.client, fetch=False)
        # this pass's sequence emails (replies the agent sent meanwhile are older); one per address
        new = {m["To"]: m for m in self.outbox.messages[before:]}
        self.sends.extend(sent)
        for s in sent:
            sim = self._by_id.get(s.get("lead_id") or -1)
            msg = new.get(s.get("email"))
            if sim is None:
                continue  # a lead the agent created itself (a referral): nobody behind it
            links = self._links(msg)
            step = self._real_step(sim.lead_id, s["step"])
            local = s["sent_at"].astimezone(ZoneInfo("Asia/Kolkata")).strftime("%a %d %b %H:%M")
            sim.log.append(f"email {step} delivered ({local} India time)")
            for act in sim.persona.inbox.machines:
                heapq.heappush(self._queue, (s["sent_at"] + act.after, next(self._seq), sim, act, step, links))
            for i, act in enumerate(sim.persona.acts):
                if act.step == step and i not in sim.done:
                    sim.done.add(i)
                    heapq.heappush(self._queue, (s["sent_at"] + act.after, next(self._seq), sim, act, step, links))

    def _real_step(self, lead_id: int, campaign_step: int) -> int:
        """The sequence step of a send: a lead re-enrolled mid-sequence (after an out-of-office)
        restarts the campaign at its step 1, recorded as variant_flags["step_offset"]."""
        with self.factory() as s:
            lead = s.get(Lead, lead_id)
            offset = int((lead.variant_flags or {}).get("step_offset", 0)) if lead is not None else 0
        return campaign_step + offset

    def _links(self, msg: Any) -> Dict[str, str]:
        """tag -> path for every tracked link and the image in the email as it was sent."""
        if msg is None:
            return {}
        text = " ".join(part.get_content() for part in msg.walk()
                        if part.get_content_type() in ("text/plain", "text/html"))
        out: Dict[str, str] = {}
        for url in _LINK.findall(text):
            path = urlsplit(url).path
            if path.startswith("/o/"):
                out["pixel"] = path
                continue
            parsed = parse_token(path.rsplit("/", 1)[-1], settings=self.settings)
            if parsed is not None:
                out.setdefault(parsed[2], path)
        return out

    def _run_due_acts(self) -> None:
        while self._queue and self._queue[0][0] <= self.clock.now:
            _, _, sim, act, step, links = heapq.heappop(self._queue)
            try:
                self._do(sim, act, step, links)
            except Exception as exc:  # noqa: BLE001 - one broken act must not stop the world
                sim.log.append(f"(simulation error on {act.kind}: {type(exc).__name__})")
                logger.warning("world act %s failed for lead %s: %s", act.kind, sim.lead_id, type(exc).__name__)

    # ------------------------------------------------------------ acts
    def _req(self, method: str, path: str, ip: str, ua: str, body: bytes = b"",
             headers: Optional[Dict[str, str]] = None) -> Any:
        h = {"User-Agent": ua, "X-Forwarded-For": ip, **(headers or {})}
        return self.client.request(method, path, headers=h, content=body)

    def _sim(self, sim: SimLead, step: int, email: Optional[str] = None) -> devsim.SimLead:
        return devsim.SimLead(id=sim.lead_id, email=email or sim.email, step_sent=step, active_channel="email")

    def _post(self, req: devsim.SimRequest) -> Any:
        return self.client.request(req.method, req.path, headers=req.headers, content=req.body)

    def _click(self, sim: SimLead, step: int, tag: str, path: str, ip: str, ua: str, js: bool) -> None:
        r = self._req("GET", path, ip, ua)
        target = r.headers.get("location") if r.status_code in (301, 302, 303, 307) else devsim.click_target(r.text)
        if js:
            beacon = devsim.click_confirm(r.text, ua)
            if beacon is not None:
                self._req("POST", beacon.path, ip, ua)
        if target:
            sim.targets[tag] = target

    def _do(self, sim: SimLead, act: Act, step: int, links: Dict[str, str]) -> None:
        person = sim.persona.inbox
        if act.kind == "scan":
            self._scan(sim, act, step, links)
        elif act.kind == "read":
            sim.log.append(f"person reads email {step}")
            if person.open_ip:
                self._req("GET", links.get("pixel") or self._pixel(sim, step), person.open_ip, person.open_ua)
        elif act.kind == "click":
            sim.person_clicks += 1
            sim.log.append(f"person clicks {act.tag} in email {step}")
            path = links.get(act.tag) or urlsplit(make_link(sim.lead_id, step, act.tag, settings=self.settings)).path
            self._click(sim, step, act.tag, path, person.person_ip, devsim.BROWSER_UA, js=True)
        elif act.kind == "visit":
            sim.log.append(f"person reads the {act.tag} page for {act.seconds}s")
            token = self._visit_token(sim, step, act.tag)
            req = devsim.visit(self.settings, self._sim(sim, step), tag=act.tag, step=step, active_s=act.seconds,
                               scroll_pct=act.scroll, interactions=max(1, act.seconds // 4))
            self._req("POST", f"/v/{token}", person.person_ip, devsim.BROWSER_UA, req.body,
                      {"Content-Type": "text/plain"})
        elif act.kind == "reply":
            sim.log.append(f"person replies to email {step}")
            self._post(devsim.email_reply(self.settings, self._sim(sim, step), self.clock.now,
                                          text=act.text.format(colleague=sim.colleague)))
        elif act.kind == "bounce":
            sim.log.append(f"email {step} bounces")
            self._post(devsim.email_bounce(self.settings, self._sim(sim, step), self.clock.now, hard=True))
        elif act.kind == "unsubscribe":
            sim.log.append(f"person unsubscribes from email {step}")
            path = links.get("unsubscribe") or urlsplit(
                make_link(sim.lead_id, step, "unsubscribe", settings=self.settings)).path
            self._click(sim, step, "unsubscribe", path, person.person_ip, devsim.BROWSER_UA, js=True)
            req = devsim.unsubscribe_link(self.settings, self._sim(sim, step), step=step)
            self._req("POST", req.path, person.person_ip, devsim.BROWSER_UA)
        elif act.kind == "book":
            sim.log.append("person books a demo")
            self._post(devsim.cal_event(self.settings, self._sim(sim, step), self.clock.now, "cal-booked",
                                        uid=f"world-{sim.lead_id}"))

    def _scan(self, sim: SimLead, act: Act, step: int, links: Dict[str, str]) -> None:
        wanted = act.tags or tuple(links)
        hits = []
        for tag in wanted:
            path = links.get(tag)
            if path is None or (tag == TRAP_TAG and not act.trap):
                continue
            if tag == "pixel":
                self._req("GET", path, act.ip, act.user_agent or devsim.BROWSER_UA)
            else:
                sim.machine_clicks += 1
                self._click(sim, step, tag, path, act.ip, act.user_agent or devsim.BROWSER_UA, act.js)
            hits.append(tag.replace("_", " "))
            self.clock.now += timedelta(seconds=act.gap_s)
        if hits:
            sim.log.append(f"machine at {act.ip} opens {', '.join(hits)} of email {step}")

    def _pixel(self, sim: SimLead, step: int) -> str:
        return urlsplit(make_open_pixel(sim.lead_id, step, settings=self.settings)).path

    def _visit_token(self, sim: SimLead, step: int, tag: str) -> str:
        target = sim.targets.get(tag) or ""
        got = parse_qs(urlsplit(target).query).get(VISIT_PARAM)
        return got[0] if got else visit_token(sim.lead_id, step, tag, settings=self.settings,
                                                   issued_at=self.clock.now)


def simulate(workdir: Path, n: Optional[int] = None, seed: int = 7, days: int = DAYS) -> World:
    return World(workdir, pick_personas(n, seed)).run(days)


__all__ = ["World", "simulate", "pick_personas", "START"]
