"""Real-visit tracking: did a person actually read the page the email linked to?

A click on an email link can be a bank's security scanner. What a scanner does not do is read the
page: stay on it for a while, scroll, move the mouse or touch the screen. So the website pages our
emails link to load one small script (``/v.js``), and a tracked click forwards to them with a signed
visit token (``?oz_v=...``, see ``links.visit_token``). The script counts only *active* reading time
(the tab is visible and the reader scrolled, moved, typed or touched in the last 30 s), and once the
visit is engaged it sends one beacon to ``POST /v/<token>``.

Engaged = at least 15 s of active reading plus a scroll to 40 % of the page (or 3 interactions),
or a second page of the site with 8 s of active reading. The server re-checks the same rule; a
"visit" event then goes through the normal pipeline (settled and bot-filtered by the worker,
then ``rules._on_visit``), and an engaged visit counts as a confirmed human signal.

The website team installs the script once (docs/guides/visit-tracking.md). Until then nothing
breaks: clicks are tracked as before, visits are simply never reported.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from .emailtool.base import NormalizedEvent
from .ingest import ingest
from .links import parse_visit_token

logger = logging.getLogger(__name__)

ENGAGED_ACTIVE_S = 15
ENGAGED_SCROLL_PCT = 40
ENGAGED_INTERACTIONS = 3
MULTI_PAGE = 2
MULTI_PAGE_ACTIVE_S = 8
BEACON_MAX_BYTES = 2048
MAX_ACTIVE_S = 4 * 3600
MAX_PAGES = 100
MAX_INTERACTIONS = 999
UA_MAX = 512
HEADERS = {"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"}
JS_HEADERS = {"Cache-Control": "public, max-age=3600", "Access-Control-Allow-Origin": "*",
              "X-Content-Type-Options": "nosniff"}


@dataclass(frozen=True)
class VisitStats:
    active_s: int
    scroll_pct: int
    pages: int
    interactions: int


def _bounded_int(value: Any, low: int, high: int) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value:  # NaN
        return None
    n = int(value)
    return n if low <= n <= high else None


def parse_beacon(body: bytes) -> Optional[VisitStats]:
    """The script's JSON {"a": active s, "s": scroll %, "p": pages, "i": interactions}. None when
    malformed or out of range; never raises."""
    if not body or len(body) > BEACON_MAX_BYTES:
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    active = _bounded_int(data.get("a"), 0, MAX_ACTIVE_S)
    scroll = _bounded_int(data.get("s"), 0, 100)
    pages = _bounded_int(data.get("p"), 1, MAX_PAGES)
    inter = _bounded_int(data.get("i"), 0, MAX_INTERACTIONS)
    if active is None or scroll is None or pages is None or inter is None:
        return None
    return VisitStats(active, scroll, pages, inter)


def is_engaged(st: VisitStats) -> bool:
    """Same rule as the script: real reading, not a page fetch."""
    read = st.active_s >= ENGAGED_ACTIVE_S and (st.scroll_pct >= ENGAGED_SCROLL_PCT
                                                or st.interactions >= ENGAGED_INTERACTIONS)
    browsed = st.pages >= MULTI_PAGE and st.active_s >= MULTI_PAGE_ACTIVE_S
    return read or browsed


def _network(ip: Optional[str]) -> str:
    """The visitor's /24 (IPv4) or /48 (IPv6), as text: the same office or scanner farm."""
    if not ip:
        return "-"
    if ":" in ip:
        return ":".join(ip.split(":")[:3])
    return ".".join(ip.split(".")[:3])


def visit_event(lead_id: int, step: int, tag: str, channel: str, st: VisitStats, now: datetime,
                ip: Optional[str], user_agent: Optional[str]) -> NormalizedEvent:
    """One engaged visit per lead, email step, page, day and network (later beacons are duplicates).
    The network is part of the key so a scanner's beacon (marked bot) can't swallow the person's."""
    return NormalizedEvent(
        provider_event_id=f"visit:{lead_id}:{step}:{tag}:{channel}:{now.date().isoformat()}:{_network(ip)}",
        channel=channel, kind="visit", occurred_at=now, step=step, tag=tag, lead_id=lead_id,
        ip=ip, user_agent=user_agent[:UA_MAX] if user_agent else None,
        data={"active_s": st.active_s, "scroll_pct": st.scroll_pct, "pages": st.pages,
              "interactions": st.interactions},
    )


# The site snippet. ES5, no dependencies, never throws into the host page. It finds our server from
# its own src, keeps the token for the tab's session (so a second page still counts), removes
# oz_v from the address bar (so it is not shared onwards), and sends one beacon once engaged.
# Keep the thresholds in step with is_engaged above.
VISIT_JS = """(function () {
  try {
    var me = document.currentScript && document.currentScript.src;
    if (!me || !window.sessionStorage || !window.JSON) return;
    var base = me.replace(/\\/v\\.js(\\?.*)?$/, ""), K = "oz_v", ss = window.sessionStorage;
    var m = /[?&]oz_v=([A-Za-z0-9_\\-]+\\.[0-9a-f]+)/.exec(location.search), t = m && m[1];
    if (t) {
      ss.setItem(K, t); ss.setItem(K + "_p", "0"); ss.removeItem(K + "_sent");
      var q = location.search.replace(/([?&])oz_v=[^&#]*&?/, "$1").replace(/[?&]$/, "");
      try { history.replaceState(history.state, "", location.pathname + q + location.hash); } catch (e) {}
    } else {
      t = ss.getItem(K);
    }
    if (!t || ss.getItem(K + "_sent")) return;
    var pages = parseInt(ss.getItem(K + "_p") || "0", 10) || 0;
    if (ss.getItem(K + "_last") !== location.pathname) {  // a reload or going back is not a new page
      pages += 1; ss.setItem(K + "_p", String(pages)); ss.setItem(K + "_last", location.pathname);
    }
    var active = 0, last = Date.now(), lastInput = 0, inter = 0, scroll = 0, done = false;
    function depth() {
      var d = document.documentElement, b = document.body;
      var full = Math.max(d.scrollHeight, b ? b.scrollHeight : 0);
      var seen = (window.pageYOffset || d.scrollTop || 0) + window.innerHeight;
      var p = full > 0 ? Math.min(100, Math.round(seen * 100 / full)) : 100;
      if (p > scroll) scroll = p;
    }
    function input(e) {
      lastInput = Date.now();  // any input keeps the reading time going...
      if (e && e.type !== "mousemove" && inter < 999) inter++;  // ...but a mouse jiggle is not an interaction
      if (e && e.type === "scroll") depth();
    }
    var evs = ["scroll", "mousemove", "pointerdown", "touchstart", "keydown", "wheel"];
    for (var i = 0; i < evs.length; i++) window.addEventListener(evs[i], input, { passive: true });
    function engaged() {
      return (active >= 15 && (scroll >= 40 || inter >= 3)) || (pages >= 2 && active >= 8);
    }
    function send() {
      var body = JSON.stringify({ a: Math.round(active), s: scroll, p: pages, i: inter });
      var url = base + "/v/" + t;
      var ok = false;
      if (navigator.sendBeacon) ok = navigator.sendBeacon(url, new Blob([body], { type: "text/plain" }));
      if (!ok && window.fetch) { fetch(url, { method: "POST", body: body, keepalive: true, mode: "no-cors" }); ok = true; }
      if (ok) ss.setItem(K + "_sent", "1");
      return ok;  // a full beacon queue: try again on the next second
    }
    var timer = setInterval(function () {
      var now = Date.now();
      if (document.visibilityState !== "hidden" && now - lastInput < 30000) active += (now - last) / 1000;
      last = now;
      if (!done && engaged() && send()) { done = true; clearInterval(timer); }
    }, 1000);
  } catch (e) {}
})();
"""


async def _read_body(request: Request) -> Optional[bytes]:
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > BEACON_MAX_BYTES):
        return None
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > BEACON_MAX_BYTES:
            return None
    return bytes(buf)


def register_visit_routes(app: FastAPI, *, settings: Any, clock: Callable[[], datetime],
                          session_factory: Callable[[], Any], deps: Any, scanners: Any,
                          client_ip: Callable[[Request, bool], Optional[str]]) -> None:
    """GET /v.js (the site snippet) and POST /v/<token> (its beacon). The beacon answers 204
    whatever happens: the visitor's page must never see an error."""

    @app.get("/v.js")
    def visit_script() -> Response:
        return Response(VISIT_JS, media_type="application/javascript", headers=JS_HEADERS)

    @app.post("/v/{token}")
    async def visit_beacon(token: str, request: Request) -> Response:
        if not settings.visit_tracking:
            return Response(status_code=204, headers=HEADERS)
        now = clock()
        parsed = parse_visit_token(token, settings=settings, now=now)
        body = await _read_body(request) if parsed is not None else None
        stats = parse_beacon(body) if body is not None else None
        if parsed is None or stats is None or not is_engaged(stats):
            return Response(status_code=204, headers=HEADERS)
        lead_id, step, tag, channel = parsed
        ev = visit_event(lead_id, step, tag, channel, stats, now,
                         client_ip(request, settings.trust_proxy_headers), request.headers.get("user-agent"))

        def store() -> None:
            session = session_factory()
            try:
                ingest(session, [ev], deps, now, scanners)
            finally:
                session.close()

        try:
            await run_in_threadpool(store)
            logger.info("engaged visit lead %s step %s tag %s (%ss active)", lead_id, step, tag, stats.active_s)
        except Exception as exc:  # noqa: BLE001 - the beacon never errors back to the page
            logger.error("visit store failed for lead %s: %s", lead_id, type(exc).__name__)
        return Response(status_code=204, headers=HEADERS)
