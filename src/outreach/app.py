"""FastAPI app: email, WhatsApp and Cal.com webhooks, tracked-link redirect, scanner-safe unsubscribe,
intake, Microsoft Teams sales commands (outgoing webhook: wa / done / retry / nurture / close / status /
help), health."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import html
import json
import logging
import re
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from .actions import (
    TOOL_BLOCK_PENDING, TOOL_STOP_PENDING, Deps, SetState, StopAllOutreach, execute, record_integration_error,
)
from .alerts import ConsoleAlerter, TeamsAlerter
from .booking import make_handoff, parse_cal_webhook
from .botfilter import Scanners, load_scanners
from .chatbot import make_bot
from .classifier import make_classifier
from .config import Settings, get_settings
from .db import init_db, make_engine
from .emailtool.base import BadSignature, NormalizedEvent
from .emailtool.fake import FakeEmailTool
from .emailtool.instantly import InstantlyTool
from .mailpit_sender import make_tool as make_mailpit_tool
from .handoff import start_takeover
from .ingest import IP_MAX, UA_MAX, ingest
from .intake import intake_lead
from .jobs import JobQueue, run_job
from .links import TRAP_TAG, VISIT_TAGS, lead_token, parse_open_token, parse_token, visit_token, with_visit_token
from .llm import make_llm
from .llm.local_fake import RuleBasedLLM
from .localclock import default_clock
from .logging_setup import configure as configure_logging
from .models import Event, Lead
from .outreach import (
    FAILED_SUFFIX, INTRO_ATTEMPT, REMINDER_ATTEMPT, TRANSIENT_SUFFIX, WA_RETRY_AFTER, Outreach,
)
from .states import ALLOWED, TERMINAL_VALUES, LeadState
from .richmail import IMAGE_RE, STATIC_DIR
from .visits import register_visit_routes
from .unsubscribe_page import CONFIRM_HTML, DONE_HTML, ERROR_HTML, HEADERS as PAGE_HEADERS, NOT_FOUND_HTML
from .verify import make_verifier
from .whatsapp.client import WaSendError, WhatsAppClient, WhatsAppNotAllowed, WindowClosed
from .whatsapp.fake import FakeWhatsAppClient
from .whatsapp.sender import WhatsAppSender, check_free_text
from .whatsapp.webhook import parse_wa_webhook

logger = logging.getLogger(__name__)

MAX_BODY = 1024 * 1024  # 1 MiB
MAX_CHALLENGE_LEN = 64
DEFAULT_HOME = "https://ozoo.ai/"
NO_STORE = {"Cache-Control": "no-store"}
# Click confirmation page (CLICK_JS_CONFIRM): a person's browser runs the beacon, then goes on;
# most link scanners fetch the page without running JavaScript. A beacon must arrive within this.
CLICK_CONFIRM_WINDOW_S = 600
TRAP_HTML = "<!doctype html><title>OK</title><p>OK</p>"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _click_page(target: str, confirm_path: str, nonce: str) -> str:
    def js(v: str) -> str:  # a JS string literal that cannot close the <script> element
        return json.dumps(v).replace("<", "\\u003c")
    href = html.escape(target, quote=True)
    return (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="robots" content="noindex">'
        '<meta name="referrer" content="no-referrer"><title>Opening…</title>'
        f'<noscript><meta http-equiv="refresh" content="0;url={href}"></noscript></head>'
        '<body style="font-family:Arial,sans-serif;color:#555;padding:24px">'
        f'<p>Opening… <a href="{href}">continue</a></p>'
        f'<script nonce="{nonce}">(function(){{var t={js(target)},done=false;'
        'function go(){if(!done){done=true;location.replace(t);}}'
        f'try{{fetch({js(confirm_path)},{{method:"POST",keepalive:true,credentials:"omit"}})'
        '.then(go,go);}catch(e){go();}setTimeout(go,1200);})();</script></body></html>')


def _click_page_headers(nonce: str) -> Dict[str, str]:
    return {"Cache-Control": "no-store", "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; "
                                        "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; "
                                        "frame-ancestors 'none'")}
# 1x1 transparent GIF for the open pixel (/o/<token>.gif)
PIXEL_GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00"
             b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
PIXEL_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"}
OPEN_DEDUPE_S = 300  # repeated loads of the same email within 5 minutes count as one open
TEAMS_MAX_BODY = 64 * 1024
TEAMS_MAX_AGE_S = 300
TEAMS_SIG_MAX_LEN = 128
TEAMS_ID_MAX = 190  # Event.provider_event_id is 200 with the "teams:" prefix
TEAMS_TS_MAX_LEN = 40
WA_TEXT_MAX = 4096
OWNER_MAX = 100  # Lead.wa_human_owner / wa_human_owner_name column size
TEAMS_USAGE = ("Commands: wa LEAD_ID MESSAGE (send the lead a WhatsApp message and pause the bot), "
               "done LEAD_ID (give the chat back to the bot), "
               "retry LEAD_ID (clear failed-start flags so the worker starts the lead again), "
               "nurture LEAD_ID (stop all outreach, move to nurture), "
               "close LEAD_ID (stop all outreach, close the lead), "
               "status LEAD_ID (state, channel and key flags), help.")
# flags the retry command clears: start / send attempts and failures (start_new then tries again)
RETRY_CLEARS = tuple(["start_attempts", "start_retry_at", "start_failed", WA_RETRY_AFTER, "stuck_alerted"] + [
    k + suffix for k in (INTRO_ATTEMPT, REMINDER_ATTEMPT)
    for suffix in ("", FAILED_SUFFIX, "_unconfirmed", TRANSIENT_SUFFIX)])
# flag names shown by the status command (names only, never values that could hold contacts)
STATUS_FLAGS = ("start_failed", "start_attempts", WA_RETRY_AFTER, INTRO_ATTEMPT, REMINDER_ATTEMPT,
                TOOL_STOP_PENDING, TOOL_BLOCK_PENDING, "stuck_alerted", "takeover_at", "hot_alerted")
NOT_ALLOWED = "Not allowed here."
ALREADY_HANDLED = "Already handled."
WINDOW_CLOSED_TEXT = "24h window closed — the lead must message first"
NO_CONSENT_TEXT = "lead has opted out / no WhatsApp consent"
INTERNAL_ERROR_TEXT = "Internal error — nothing was changed; try again."
_TEAMS_TS = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})\Z")
_AT_MENTION = re.compile(r"<at\b[^>]*>.*?</at>", re.IGNORECASE | re.DOTALL)
_LINE_BREAK = re.compile(r"<br\s*/?>|</p>|</div>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]*>")
_COMMAND = re.compile(r"(\S+)(?:\s+(.*))?\Z", re.DOTALL)
_WA_ARGS = re.compile(r"(\S+)\s+(.+)\Z", re.DOTALL)

# (lead_id, message) for the background WhatsApp send
_WaJob = Tuple[int, str]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _BodyTooLarge(Exception):
    pass


class _BadRequest(Exception):
    pass


async def _read_capped(request: Request, cap: int = MAX_BODY) -> bytes:
    """Reject on declared Content-Length and on actual streamed length."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > cap:
                raise _BodyTooLarge()
        except ValueError as exc:
            raise _BadRequest() from exc
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > cap:
            raise _BodyTooLarge()
    return bytes(buf)


def _fail(status: int) -> JSONResponse:
    return JSONResponse({"ok": False}, status_code=status)


def default_deps(settings: Settings) -> Deps:
    """Real providers; with APP_ENV=local each service set to "fake" (EMAIL_MODE, WHATSAPP_MODE,
    LLM_MODE, ALERTS_MODE) gets its offline stand-in instead. Settings refuse fake in production."""
    run_id = uuid.uuid4().hex[:8]  # fake ids never repeat across processes / restarts
    if settings.email_mode == "fake":
        tool: Any = FakeEmailTool(webhook_secret=settings.tool_webhook_secret, log_calls=True,
                                  id_prefix=f"fake-{run_id}-")
    elif settings.email_mode in ("mailpit", "gmail", "mailbox"):
        # mailpit: local SMTP to Mailpit; gmail: one real Gmail mailbox; mailbox: our own SMTP / IMAP
        # mailboxes (production too). The mailpit_sender process sends the steps and reads replies.
        tool = make_mailpit_tool(settings)
    elif settings.tool_name != "instantly":
        raise ValueError(f"no adapter for tool {settings.tool_name!r} yet")
    else:
        tool = InstantlyTool(settings.tool_api_key, settings.tool_webhook_secret)
    wa_client: Any = (FakeWhatsAppClient(log_sends=True, id_prefix=f"wamid.FAKE{run_id}.")
                      if settings.whatsapp_mode == "fake" else WhatsAppClient(settings))
    alerter: Any = ConsoleAlerter() if settings.alerts_mode == "fake" else TeamsAlerter(settings.teams_webhook_url)
    # one client for the classifier and the chatbot (LLM_PROVIDER)
    llm = RuleBasedLLM() if settings.llm_mode == "fake" else make_llm(settings)
    outreach = Outreach(settings)
    deps = Deps(
        tool=tool,
        wa=WhatsAppSender(wa_client, settings),  # consent / window / suppression checks always run
        alerter=alerter,
        outreach=outreach,
        classify=make_classifier(settings, llm),
        bot_reply=make_bot(settings, llm),
        jobs=JobQueue(),  # LLM calls never run inside a webhook request
    )
    outreach.bind(deps)
    return deps


def verify_teams_signature(authorization: Optional[str], body: bytes, key: bytes) -> bool:
    """Teams outgoing webhook: "Authorization: HMAC <base64(HMAC-SHA256(key, raw body))>", where key
    is the base64-decoded security token. Constant-time compare on bytes."""
    if not authorization or not authorization.startswith("HMAC "):
        return False
    provided_b64 = authorization[len("HMAC "):].strip()
    if not provided_b64 or len(provided_b64) > TEAMS_SIG_MAX_LEN:
        return False
    try:
        provided = base64.b64decode(provided_b64, validate=True)
    except (binascii.Error, ValueError):
        return False
    expected = hmac.new(key, body, hashlib.sha256).digest()
    return hmac.compare_digest(expected, provided)


def _parse_teams_ts(raw: Any) -> Optional[datetime]:
    """ISO 8601 with a zone; Teams sends 7 fractional digits ("...:00.1234567Z"), which
    datetime.fromisoformat on Python 3.10 cannot read, so the fraction is cut to 6."""
    if not isinstance(raw, str) or len(raw) > TEAMS_TS_MAX_LEN:
        return None
    m = _TEAMS_TS.match(raw)
    if m is None:
        return None
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    zone = "+00:00" if m.group(3) == "Z" else m.group(3)
    try:
        return datetime.fromisoformat(f"{m.group(1)}.{frac}{zone}")
    except ValueError:
        return None


def _dig(obj: Any, *keys: str) -> Optional[str]:
    """obj[k1][k2]... if every level is a dict and the leaf a non-empty string, else None."""
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj if isinstance(obj, str) and obj else None


def _teams_channel_id(act: Dict[str, Any]) -> Optional[str]:
    channel = _dig(act, "channelData", "channel", "id")
    if channel:
        return channel
    conv = _dig(act, "conversation", "id")  # "19:...@thread.tacv2;messageid=..."
    return conv.split(";", 1)[0] if conv else None


def _command_text(raw: str) -> str:
    """Strip <at>…</at> mentions and any other HTML Teams adds, unescape entities, trim."""
    text = _AT_MENTION.sub(" ", raw)
    text = _LINE_BREAK.sub("\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text).replace("\xa0", " ")
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def _lead_id_arg(raw: str) -> Optional[int]:
    return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 18 else None


BOOKING_LEAD_PARAM = "metadata[lead]"  # Cal.com stores metadata[...] query params on the booking


def with_lead_token(target: str, lead_id: int, settings: Settings) -> str:
    """The configured booking URL plus metadata[lead]=<signed lead token>. Only the query changes
    (scheme / host / path come from config), so this can never become an open redirect."""
    parts = urlsplit(target)
    # keep the configured query byte for byte (Microsoft Bookings uses bare flags like ?anonymous&ep=…)
    ours = (BOOKING_LEAD_PARAM, quote(BOOKING_LEAD_PARAM, safe=""))
    kept = [p for p in parts.query.split("&") if p and unquote(p.split("=", 1)[0]) not in ours]
    kept.append(urlencode([(BOOKING_LEAD_PARAM, lead_token(lead_id, settings=settings))]))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), parts.fragment))


def _client_ip(request: Request, trust_proxy: bool = False) -> Optional[str]:
    """The clicker's IP for the bot filter. X-Forwarded-For is client-controlled: read it only when
    TRUST_PROXY_HEADERS=1 (behind a proxy you run that sets it); else the socket peer."""
    if trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            first = fwd.split(",")[0].strip()
            if first:
                return first[:IP_MAX]
    return request.client.host[:IP_MAX] if request.client else None


def wire_deps(deps: Deps, settings: Settings, verifier: Any) -> Deps:
    """Fill the ports create_app and the worker CLI both need (unless a test set them already)."""
    if deps.jobs is None:
        deps.jobs = JobQueue()  # always: classify / bot replies run after the response, never in it
    if deps.intake is None:
        # referrals from "wrong person" replies go through the same intake as POST /intake
        def _referral_intake(session: Session, payload: Dict[str, Any], now: datetime) -> Any:
            return intake_lead(session, payload, verifier, now, primary_when_both=settings.primary_when_both,
                               eu_email_approved=settings.eu_email_approved, tz_groups=settings.campaign_ids,
                               default_tz_group=settings.default_tz_group,
                               default_phone_region=settings.default_phone_region)

        deps.intake = _referral_intake
    if deps.handoff is None:
        deps.handoff = make_handoff(deps.alerter)
    if deps.verify is None:
        deps.verify = getattr(verifier, "check", None)
    if deps.takeover is None:
        def _takeover(session: Session, lead: Lead, reason: str, now: datetime) -> None:
            start_takeover(session, lead, reason, deps, now)

        deps.takeover = _takeover
    if isinstance(deps.outreach, Outreach) and deps.outreach.deps is None:
        deps.outreach.bind(deps)
    return deps


WORKER_STOP_TIMEOUT_S = 30.0


def create_app(settings: Optional[Settings] = None, deps: Optional[Deps] = None, engine: Any = None, *,
               clock: Callable[[], datetime] = _utcnow, scanners: Optional[Scanners] = None,
               verifier: Any = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()  # the outreach logger at LOG_LEVEL (ids and kinds only)
    if settings.app_env == "local":
        if clock is _utcnow:
            clock = default_clock(settings)  # the fake clock (python -m outreach.dev advance)
    if engine is None:
        engine = make_engine(settings.database_url)
    init_db(engine)  # idempotent create_all
    deps = deps or default_deps(settings)
    scanners = scanners or load_scanners()
    verifier = verifier or make_verifier(settings)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    wire_deps(deps, settings, verifier)
    home = settings.link_targets.get("homepage") or DEFAULT_HOME
    teams_key = settings.teams_outgoing_key
    teams_allowed = settings.teams_allowed_aad_id_set
    teams_tenant = settings.teams_tenant_id.strip().lower()  # GUID: case-insensitive
    teams_channel = settings.teams_sales_channel_id.strip()

    async def _worker_loop(stop: asyncio.Event, tick_stop: threading.Event) -> None:
        from . import worker  # looked up per tick (tests replace worker.tick)

        logger.info("worker loop started (every %ss)", settings.worker_interval_s)
        while not stop.is_set():
            try:
                # in a thread: the tick does blocking DB and HTTP work
                # tick_stop: on shutdown a running tick ends after its current job
                await asyncio.to_thread(worker.tick, session_factory, deps, clock, settings, stop=tick_stop)
            except Exception as exc:  # noqa: BLE001 - the loop must survive any tick failure
                logger.error("worker tick crashed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_interval_s)
            except asyncio.TimeoutError:
                pass
        logger.info("worker loop stopped")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        tick_stop = threading.Event()
        task: Optional[asyncio.Task] = None
        if settings.run_worker:
            task = asyncio.create_task(_worker_loop(stop, tick_stop))
            app.state.worker_task = task
        try:
            yield
        finally:
            if task is not None:
                stop.set()
                tick_stop.set()
                try:
                    await asyncio.wait_for(task, timeout=WORKER_STOP_TIMEOUT_S)
                except asyncio.TimeoutError:
                    logger.warning("worker loop did not stop within %ss; cancelled", WORKER_STOP_TIMEOUT_S)
                except Exception as exc:  # noqa: BLE001 - shutdown must go on
                    logger.error("worker loop failed on shutdown: %s", type(exc).__name__)

    app = FastAPI(title="Ozoo outreach agent", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)

    def get_session() -> Iterator[Session]:
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    def _run_jobs(job_ids: List[int]) -> None:
        """BackgroundTask after the webhook response: run each job in its own session (run_job
        never raises). Anything left over (crash, deploy) is picked up by the worker's run_due."""
        for job_id in job_ids:
            try:
                run_job(session_factory, job_id, deps, clock)
            except Exception as exc:  # noqa: BLE001 - a background task must never raise
                logger.error("job %s: background run crashed: %s", job_id, type(exc).__name__)

    def _schedule(background: BackgroundTasks, job_ids: List[int]) -> None:
        if job_ids:
            background.add_task(_run_jobs, list(job_ids))

    if settings.app_env == "local":  # live demo console at /demo; never in production
        from . import demo_console, schedule_console, template_console

        demo_console.register(app, session_factory, clock, settings)
        template_console.register(app, session_factory, clock, settings, deps.tool)
        schedule_console.register(app, session_factory, clock, settings, deps.tool)
        from .p0 import console as p0_console

        p0_console.register(app, session_factory, clock, settings, deps)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled %s on %s", type(exc).__name__, request.url.path.split("/")[1:2])
        return _fail(500)

    @app.get("/healthz")
    def healthz() -> Dict[str, bool]:
        return {"ok": True}

    # ------------------------------------------------------------ email webhook
    @app.post("/webhooks/email")
    async def email_webhook(request: Request, background: BackgroundTasks,
                            session: Session = Depends(get_session)) -> Response:
        try:
            body = await _read_capped(request)
        except _BodyTooLarge:
            return _fail(413)
        except _BadRequest:
            return _fail(400)
        now = clock()
        try:
            events = deps.tool.parse_webhook(request.headers, body, received_at=now)
        except BadSignature:
            logger.warning("email webhook rejected: bad signature")
            return _fail(401)
        except (ValueError, RecursionError):
            logger.warning("email webhook rejected: malformed body")
            return _fail(400)
        try:
            job_ids = await run_in_threadpool(ingest, session, events, deps, now, scanners)
        except Exception as exc:  # noqa: BLE001 - provider retries; idempotency makes it safe
            logger.error("email webhook ingest failed: %s", type(exc).__name__)
            return _fail(500)
        _schedule(background, job_ids)
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------ WhatsApp webhook (Meta)
    @app.get("/webhooks/whatsapp")
    def whatsapp_verify(request: Request) -> Response:
        q = request.query_params
        token = q.get("hub.verify_token") or ""
        if q.get("hub.mode") != "subscribe" or not hmac.compare_digest(
                token.encode(), settings.wa_verify_token.encode()):
            logger.warning("whatsapp verify rejected")
            return PlainTextResponse("forbidden", status_code=403)
        challenge = q.get("hub.challenge") or ""
        if not (challenge.isascii() and challenge.isdigit() and len(challenge) <= MAX_CHALLENGE_LEN):
            return PlainTextResponse("bad challenge", status_code=400)
        return PlainTextResponse(challenge)

    @app.post("/webhooks/whatsapp")
    async def whatsapp_webhook(request: Request, background: BackgroundTasks,
                               session: Session = Depends(get_session)) -> Response:
        try:
            body = await _read_capped(request)
        except _BodyTooLarge:
            return _fail(413)
        except _BadRequest:
            return _fail(400)
        try:
            events = parse_wa_webhook(request.headers, body, settings.wa_app_secret)
        except BadSignature:
            logger.warning("whatsapp webhook rejected: bad signature")
            return _fail(401)
        except (ValueError, RecursionError):
            logger.warning("whatsapp webhook rejected: malformed body")
            return _fail(400)
        try:
            job_ids = await run_in_threadpool(ingest, session, events, deps, clock(), scanners)
        except Exception as exc:  # noqa: BLE001 - Meta retries; idempotency makes it safe
            logger.error("whatsapp webhook ingest failed: %s", type(exc).__name__)
            return _fail(500)
        _schedule(background, job_ids)
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------ Cal.com webhook
    @app.post("/webhooks/cal")
    async def cal_webhook(request: Request, background: BackgroundTasks,
                          session: Session = Depends(get_session)) -> Response:
        try:
            body = await _read_capped(request)
        except _BodyTooLarge:
            return _fail(413)
        except _BadRequest:
            return _fail(400)
        now = clock()
        try:
            events = parse_cal_webhook(request.headers, body, settings.cal_webhook_secret,
                                       received_at=now, settings=settings)
        except BadSignature:
            logger.warning("cal webhook rejected: bad signature")
            return _fail(401)
        except (ValueError, RecursionError):
            logger.warning("cal webhook rejected: malformed body")
            return _fail(400)
        try:
            job_ids = await run_in_threadpool(ingest, session, events, deps, now, scanners)
        except Exception as exc:  # noqa: BLE001 - Cal.com may retry; idempotency makes it safe
            logger.error("cal webhook ingest failed: %s", type(exc).__name__)
            return _fail(500)
        _schedule(background, job_ids)
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------ tracked links
    @app.get("/r/{token}")
    def redirect(token: str, request: Request, session: Session = Depends(get_session)) -> Response:
        parsed = parse_token(token, settings=settings)
        if parsed is None:
            return RedirectResponse(home, status_code=302, headers=NO_STORE)
        lead_id, step, tag, channel = parsed
        now = clock()
        # email links (not WhatsApp: its preview fetcher is told apart by user agent) open a page that
        # confirms the click with a JavaScript beacon; the trap link is never forwarded anywhere
        js_check = bool(settings.click_js_confirm) and channel == "email" and tag != TRAP_TAG
        event_id: Optional[int] = None
        try:
            exists = session.get(Lead, lead_id) is not None
            ua = request.headers.get("user-agent")
            ev = Event(
                lead_id=lead_id if exists else None, channel=channel, kind="click", tag=tag, step=step,
                occurred_at=now, received_at=now, ip=_client_ip(request, settings.trust_proxy_headers),
                user_agent=ua[:UA_MAX] if ua else None,
                # every click waits for the worker's human/bot verdict; unsubscribe visits and trap hits
                # are only labelled, never acted on (ruling 9: only the confirm button unsubscribes)
                processed=False, data={"js_check": True} if js_check else {},
            )
            session.add(ev)
            session.commit()
            event_id = ev.id
            logger.info("click lead %s step %s tag %s", lead_id, step, tag)
        except Exception as exc:  # noqa: BLE001 - the visitor must always be redirected
            session.rollback()
            logger.error("click store failed for lead %s: %s", lead_id, type(exc).__name__)
        if tag == TRAP_TAG:
            return HTMLResponse(TRAP_HTML, headers={**NO_STORE, "X-Robots-Tag": "noindex"})
        if tag == "unsubscribe":
            target = f"/u/{token}"
        elif tag == "booking" and settings.link_targets.get("booking"):
            target = with_lead_token(settings.link_targets["booking"], lead_id, settings)
        else:
            target = settings.link_targets.get(tag) or home
            if settings.visit_tracking and tag in VISIT_TAGS and settings.link_targets.get(tag):
                # the site's /v.js snippet reports whether a person then actually reads the page
                target = with_visit_token(target, visit_token(lead_id, step, tag, channel, settings=settings,
                                                             issued_at=clock()))
        if js_check and event_id is not None:
            nonce = base64.b64encode(uuid.uuid4().bytes).decode().rstrip("=")
            return HTMLResponse(_click_page(target, f"/r/{token}/ok?e={event_id}", nonce),
                                headers=_click_page_headers(nonce))
        return RedirectResponse(target, status_code=302, headers=NO_STORE)

    @app.post("/r/{token}/ok")
    def click_confirmed(token: str, e: int = 0, session: Session = Depends(get_session)) -> Response:
        """The beacon from the click page: this click ran JavaScript in a browser. The event must be
        the one this token's visit stored (same lead, step and tag), recent and not yet settled."""
        parsed = parse_token(token, settings=settings)
        if parsed is not None and e > 0:
            lead_id, step, tag, channel = parsed
            try:
                ev = session.get(Event, e)
                fresh = ev is not None and (clock() - _aware(ev.received_at)).total_seconds() <= CLICK_CONFIRM_WINDOW_S
                if (fresh and ev.kind == "click" and not ev.processed and (ev.data or {}).get("js_check")
                        and (ev.lead_id in (lead_id, None)) and ev.step == step and ev.tag == tag
                        and ev.channel == channel):
                    ev.data = {**(ev.data or {}), "js": True}
                    session.commit()
            except Exception as exc:  # noqa: BLE001 - a beacon never errors back to the browser
                session.rollback()
                logger.error("click confirm failed: %s", type(exc).__name__)
        return Response(status_code=204, headers=NO_STORE)

    # ------------------------------------------------------------ real visits (site snippet beacon)
    @app.get("/img/{name}")
    def email_image(name: str) -> Response:
        """Images for the designed emails (static/email, task 21). Public and cacheable; only file
        names matching IMAGE_RE are served, so no path can leave that folder."""
        if not IMAGE_RE.match(name) or not (STATIC_DIR / name).is_file():
            return PlainTextResponse("not found", status_code=404)
        media = "image/png" if name.endswith(".png") else "image/jpeg"
        return Response((STATIC_DIR / name).read_bytes(), media_type=media,
                        headers={"Cache-Control": "public, max-age=604800", "X-Content-Type-Options": "nosniff"})

    register_visit_routes(app, settings=settings, clock=clock, session_factory=session_factory, deps=deps,
                          scanners=scanners, client_ip=_client_ip)

    # ------------------------------------------------------------ open pixel
    @app.get("/o/{token}")
    def open_pixel(token: str, request: Request, session: Session = Depends(get_session)) -> Response:
        """Our own open tracking (mailpit / gmail modes; Instantly tracks its own). Always answers
        with the image, whatever happens, so a mail client never shows a broken picture. Opens are
        logged only (plan v2.1): the shared ingest path classifies them (Apple proxy -> machine,
        known scanners / too soon after send -> bot) and rules only count human ones."""
        parsed = parse_open_token(token, settings=settings)
        if parsed is not None:
            lead_id, step = parsed
            now = clock()
            ua = request.headers.get("user-agent")
            bucket = int(now.timestamp()) // OPEN_DEDUPE_S
            ev = NormalizedEvent(provider_event_id=f"open:{lead_id}:{step}:{bucket}", channel="email",
                                 kind="open", occurred_at=now, step=step, lead_id=lead_id,
                                 ip=_client_ip(request, settings.trust_proxy_headers),
                                 user_agent=ua[:UA_MAX] if ua else None)
            try:
                ingest(session, [ev], deps, now, scanners)
                logger.info("open lead %s step %s", lead_id, step)
            except Exception as exc:  # noqa: BLE001 - the image is always served
                session.rollback()
                logger.error("open store failed for lead %s: %s", lead_id, type(exc).__name__)
        return Response(PIXEL_GIF, media_type="image/gif", headers=PIXEL_HEADERS)

    # ------------------------------------------------------------ unsubscribe (scanner-safe)
    def _unsub_token(token: str) -> Optional[tuple]:
        parsed = parse_token(token, settings=settings)
        if parsed is None or parsed[2] != "unsubscribe":
            return None
        return parsed

    @app.get("/u/{token}")
    def unsubscribe_confirm(token: str) -> HTMLResponse:
        if _unsub_token(token) is None:
            return HTMLResponse(NOT_FOUND_HTML, status_code=404, headers=PAGE_HEADERS)
        return HTMLResponse(CONFIRM_HTML, headers=PAGE_HEADERS)

    @app.post("/u/{token}")
    def unsubscribe_do(token: str, session: Session = Depends(get_session)) -> HTMLResponse:
        # Our form and RFC 8058 one-click ("List-Unsubscribe=One-Click") behave the same, so the
        # body is never read.
        parsed = _unsub_token(token)
        if parsed is None:
            return HTMLResponse(NOT_FOUND_HTML, status_code=404, headers=PAGE_HEADERS)
        lead_id, step, _tag, channel = parsed
        now = clock()
        ev = NormalizedEvent(provider_event_id=f"unsub:{lead_id}:{step}", channel=channel,
                             kind="unsubscribe", occurred_at=now, step=step, lead_id=lead_id)
        try:
            ingest(session, [ev], deps, now, scanners)
        except Exception as exc:  # noqa: BLE001
            logger.error("unsubscribe failed for lead %s: %s", lead_id, type(exc).__name__)
            return HTMLResponse(ERROR_HTML, status_code=500, headers=PAGE_HEADERS)
        logger.info("unsubscribe processed for lead %s", lead_id)
        return HTMLResponse(DONE_HTML, headers=PAGE_HEADERS)

    # ------------------------------------------------------------ intake
    def _do_intake(session: Session, payload: Any, now: datetime) -> Any:
        result = intake_lead(session, payload, verifier, now, primary_when_both=settings.primary_when_both,
                             eu_email_approved=settings.eu_email_approved, tz_groups=settings.campaign_ids,
                             default_tz_group=settings.default_tz_group,
                               default_phone_region=settings.default_phone_region)
        session.commit()
        return result

    @app.post("/intake")
    async def intake(request: Request, session: Session = Depends(get_session)) -> Response:
        key = request.headers.get("x-api-key") or ""
        if not hmac.compare_digest(key.encode(), settings.intake_api_key.encode()):
            return _fail(401)
        try:
            body = await _read_capped(request)
            payload = json.loads(body)
        except _BodyTooLarge:
            return _fail(413)
        except (_BadRequest, ValueError, RecursionError):  # RecursionError: deeply nested JSON
            return _fail(400)
        try:
            r = await run_in_threadpool(_do_intake, session, payload, clock())
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.error("intake failed: %s", type(exc).__name__)
            return _fail(500)
        return JSONResponse(
            {"lead_id": r.lead_id, "outcome": r.outcome, "channel": r.channel, "reason": r.reason},
            status_code=201 if r.outcome == "accepted" else 200,
        )

    # ------------------------------------------------------------ Teams sales commands
    def _cmd_wa(session: Session, args: str, aad: str, name: Optional[str],
                now: datetime) -> Tuple[str, Optional[_WaJob]]:
        m = _WA_ARGS.match(args)
        lead_id = _lead_id_arg(m.group(1)) if m else None
        if m is None or lead_id is None:
            return TEAMS_USAGE, None
        message = m.group(2).strip()
        if not 1 <= len(message) <= WA_TEXT_MAX:
            return f"Message must be 1-{WA_TEXT_MAX} characters.", None
        lead = session.get(Lead, lead_id)
        if lead is None:
            return f"Lead {lead_id} not found.", None
        try:
            # checked now so the person gets a clear answer; the sender re-checks when it sends
            check_free_text(session, lead, now)
        except WindowClosed:
            logger.info("teams wa: lead %s not sent, window closed", lead_id)
            return WINDOW_CLOSED_TEXT, None
        except WhatsAppNotAllowed:
            logger.info("teams wa: lead %s not sent, no consent or suppressed", lead_id)
            return NO_CONSENT_TEXT, None
        lead.wa_human_owner = aad[:OWNER_MAX]  # mutes the bot from this commit on
        lead.wa_human_owner_name = name[:OWNER_MAX] if name else None
        logger.info("teams wa: lead %s taken over, send queued", lead_id)
        return f"Sending to lead {lead_id}…", (lead_id, message)

    def _cmd_done(session: Session, args: str, now: datetime) -> str:
        lead_id = _lead_id_arg(args) if args else None
        if lead_id is None:
            return TEAMS_USAGE
        lead = session.get(Lead, lead_id)
        if lead is None:
            return f"Lead {lead_id} not found."
        lead.wa_human_owner = None
        lead.wa_human_owner_name = None
        lead.wa_bot_turns = 0  # the bot resumes with a fresh turn budget
        flags = dict(lead.variant_flags or {})
        flags["takeover_released"] = now.isoformat()
        lead.variant_flags = flags
        logger.info("teams done: lead %s given back to the bot", lead_id)
        return f"Lead {lead_id} is back with the bot."

    def _locked_lead(session: Session, args: str) -> Tuple[Optional[Lead], Optional[str]]:
        lead_id = _lead_id_arg(args) if args else None
        if lead_id is None:
            return None, TEAMS_USAGE
        lead = session.get(Lead, lead_id, with_for_update=True, populate_existing=True)
        if lead is None:
            return None, f"Lead {lead_id} not found."
        return lead, None

    def _cmd_retry(session: Session, args: str) -> str:
        lead, err = _locked_lead(session, args)
        if lead is None:
            return err or TEAMS_USAGE
        if lead.email_hold == "verify_error" and lead.state not in TERMINAL_VALUES:
            flags = dict(lead.variant_flags or {})
            flags.pop("verify_retry", None)  # the worker re-verifies on its next tick
            lead.variant_flags = flags
            logger.info("teams retry: lead %s verification restarted", lead.id)
            return f"Lead {lead.id}: email verification will be retried on the next worker tick."
        if lead.state != LeadState.IN_SEQUENCE.value:
            return f"Lead {lead.id} is {lead.state}; retry works only for leads in in_sequence."
        flags = dict(lead.variant_flags or {})
        for key in RETRY_CLEARS:
            flags.pop(key, None)
        lead.variant_flags = flags
        logger.info("teams retry: lead %s start flags cleared", lead.id)
        return f"Lead {lead.id}: start flags cleared; it will be retried on the next worker tick."

    def _cmd_move(session: Session, args: str, to: LeadState, label: str, now: datetime) -> str:
        lead, err = _locked_lead(session, args)
        if lead is None:
            return err or TEAMS_USAGE
        frm = LeadState(lead.state)
        if frm == to:
            return f"Lead {lead.id} is already {to.value}."
        if to not in ALLOWED[frm]:
            return f"Lead {lead.id} is {frm.value}; it cannot be moved to {to.value} from there."
        execute(session, lead, [StopAllOutreach(), SetState(to, "manual")], deps, now)
        if lead.state != to.value:  # pragma: no cover - transition() refused after all
            return f"Lead {lead.id} could not be moved to {to.value}."
        logger.info("teams %s: lead %s -> %s", label, lead.id, to.value)
        return f"Lead {lead.id}: outreach stopped, now {to.value}."

    def _cmd_status(session: Session, args: str) -> str:
        lead, err = _locked_lead(session, args)
        if lead is None:
            return err or TEAMS_USAGE
        flags = lead.variant_flags or {}
        shown = [k for k in STATUS_FLAGS if flags.get(k)]
        extra = []
        if lead.email_hold:
            extra.append(f"email_hold={lead.email_hold}")
        if lead.pause_reason:
            extra.append(f"pause_reason={lead.pause_reason}")
        return (f"lead {lead.id}: state={lead.state}, channel={lead.active_channel or 'none'}, "
                f"switched={'yes' if lead.channel_switched else 'no'}, step={lead.step_sent or 0}"
                + (", " + ", ".join(extra) if extra else "")
                + f", flags: {', '.join(shown) if shown else 'none'}")

    def _teams_command(session: Session, activity_id: str, sent_at: datetime, raw_text: str, aad: str,
                       name: Optional[str], now: datetime) -> Tuple[str, Optional[_WaJob]]:
        """Replay check + command, committed together; any exception rolls both back."""
        try:
            with session.begin_nested():
                session.add(Event(lead_id=None, channel="system", kind="teams_command",
                                  provider_event_id=f"teams:{activity_id}", occurred_at=sent_at,
                                  received_at=now, processed=True, data={}))
        except IntegrityError:
            session.rollback()
            logger.info("teams command replay ignored")
            return ALREADY_HANDLED, None
        m = _COMMAND.match(_command_text(raw_text))
        verb = m.group(1).lower() if m else ""
        args = (m.group(2) or "").strip() if m else ""
        job: Optional[_WaJob] = None
        if verb == "wa":
            text, job = _cmd_wa(session, args, aad, name, now)
        elif verb == "done":
            text = _cmd_done(session, args, now)
        elif verb == "retry":
            text = _cmd_retry(session, args)
        elif verb == "nurture":
            text = _cmd_move(session, args, LeadState.NURTURE, "nurture", now)
        elif verb == "close":
            text = _cmd_move(session, args, LeadState.CLOSED, "close", now)
        elif verb == "status":
            text = _cmd_status(session, args)
        else:
            text = TEAMS_USAGE
        session.commit()
        return text, job

    def _send_in_background(job: _WaJob) -> None:
        """Own session; never raises. A failure becomes an integration_error alert in Teams."""
        lead_id, message = job
        s = session_factory()
        try:
            now = clock()
            lead = s.get(Lead, lead_id)
            if lead is None:
                return
            try:
                deps.wa.send_raw_text(s, lead, message, now, "human")  # logs the ChatMessage
                s.commit()
                logger.info("teams wa: lead %s message sent by a person", lead_id)
                return
            except WindowClosed:
                s.rollback()
                reason = "the 24h window closed"
            except WhatsAppNotAllowed:
                s.rollback()
                reason = "the lead has opted out / no WhatsApp consent"
            except WaSendError as exc:
                s.rollback()
                reason = f"Meta error code {exc.code}, HTTP {exc.status}"
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                reason = type(exc).__name__
            logger.warning("teams wa: lead %s send failed", lead_id)
            text = (f"lead {lead_id}: the WhatsApp message typed in Teams was NOT sent ({reason}). "
                    f"Nothing reached the lead.")
            try:
                deps.alerter.send("integration_error", text, s.get(Lead, lead_id), now, s)
                s.commit()
            except Exception as exc:  # noqa: BLE001 - fall back to a plain row for the digest
                s.rollback()
                logger.warning("teams wa: lead %s failure alert failed: %s", lead_id, type(exc).__name__)
                record_integration_error(s, lead_id, text, now)
                s.commit()
        except Exception as exc:  # noqa: BLE001 - a background task must never raise
            s.rollback()
            logger.error("teams wa: lead %s background send crashed: %s", lead_id, type(exc).__name__)
        finally:
            s.close()

    @app.post("/teams/commands")
    async def teams_commands(request: Request, background: BackgroundTasks,
                             session: Session = Depends(get_session)) -> Response:
        try:
            body = await _read_capped(request, TEAMS_MAX_BODY)
        except _BodyTooLarge:
            return _fail(413)
        except _BadRequest:
            return _fail(400)
        if not verify_teams_signature(request.headers.get("authorization"), body, teams_key):
            logger.warning("teams command rejected: bad signature")
            return _fail(401)
        try:
            act = json.loads(body)
        except (ValueError, RecursionError):  # RecursionError: deeply nested body
            return _fail(400)
        if not isinstance(act, dict):
            return _fail(400)
        activity_id = act.get("id")
        sent_at = _parse_teams_ts(act.get("timestamp"))
        raw_text = act.get("text")
        aad = (_dig(act, "from", "aadObjectId") or "").strip()
        tenant = (_dig(act, "channelData", "tenant", "id") or "").strip()
        channel = (_teams_channel_id(act) or "").strip()
        if (not isinstance(activity_id, str) or not 1 <= len(activity_id) <= TEAMS_ID_MAX or sent_at is None
                or not isinstance(raw_text, str) or not aad or not tenant or not channel):
            logger.warning("teams command rejected: missing or invalid fields")
            return _fail(400)
        now = clock()
        if abs((now - sent_at).total_seconds()) > TEAMS_MAX_AGE_S:
            logger.warning("teams command rejected: stale or future timestamp")
            return _fail(401)
        if tenant.lower() != teams_tenant:
            logger.warning("teams command refused: tenant not allowed")
            return _teams_reply(NOT_ALLOWED)
        if channel != teams_channel:
            logger.warning("teams command refused: channel not allowed")
            return _teams_reply(NOT_ALLOWED)
        if aad.lower() not in teams_allowed:
            logger.warning("teams command refused: user not allowed")
            return _teams_reply(NOT_ALLOWED)
        try:
            text, job = await run_in_threadpool(_teams_command, session, activity_id, sent_at, raw_text, aad,
                                                _dig(act, "from", "name"), now)
        except Exception as exc:  # noqa: BLE001 - Teams still needs a 200 with a readable answer
            session.rollback()
            logger.error("teams command failed: %s", type(exc).__name__)
            return _teams_reply(INTERNAL_ERROR_TEXT)
        if job is not None:
            background.add_task(_send_in_background, job)  # Teams wants the reply within 5 s
        return _teams_reply(text)

    return app


def _teams_reply(text: str) -> JSONResponse:
    """Outgoing-webhook reply, posted in the channel thread. Never carries lead phone or message."""
    return JSONResponse({"type": "message", "text": text})
