"""FastAPI app: email, WhatsApp and Cal.com webhooks, tracked-link redirect, scanner-safe unsubscribe,
intake, Microsoft Teams sales commands (outgoing webhook: wa / done / help), health."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from .actions import Deps, record_integration_error
from .alerts import TeamsAlerter
from .booking import make_handoff, parse_cal_webhook
from .botfilter import Scanners, load_scanners
from .chatbot import make_bot
from .classifier import make_classifier
from .config import Settings, get_settings
from .db import init_db, make_engine
from .emailtool.base import BadSignature, NormalizedEvent, ToolUnsupported
from .emailtool.instantly import InstantlyTool
from .handoff import start_takeover
from .ingest import IP_MAX, UA_MAX, ingest
from .intake import intake_lead
from .links import lead_token, parse_token
from .models import Event, Lead
from .unsubscribe_page import CONFIRM_HTML, DONE_HTML, ERROR_HTML, HEADERS as PAGE_HEADERS, NOT_FOUND_HTML
from .verify import EmailVerifier
from .whatsapp.client import WaSendError, WhatsAppClient, WhatsAppNotAllowed, WindowClosed
from .whatsapp.sender import WhatsAppSender, check_free_text
from .whatsapp.webhook import parse_wa_webhook

logger = logging.getLogger(__name__)

MAX_BODY = 1024 * 1024  # 1 MiB
MAX_CHALLENGE_LEN = 64
DEFAULT_HOME = "https://ozoo.ai/"
NO_STORE = {"Cache-Control": "no-store"}
TEAMS_MAX_BODY = 64 * 1024
TEAMS_MAX_AGE_S = 300
TEAMS_SIG_MAX_LEN = 128
TEAMS_ID_MAX = 190  # Event.provider_event_id is 200 with the "teams:" prefix
TEAMS_TS_MAX_LEN = 40
WA_TEXT_MAX = 4096
OWNER_MAX = 100  # Lead.wa_human_owner / wa_human_owner_name column size
TEAMS_USAGE = ("Commands: wa LEAD_ID MESSAGE (send the lead a WhatsApp message and pause the bot), "
               "done LEAD_ID (give the chat back to the bot), help.")
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


class _NotWired:
    """Placeholder port until the owning task wires the real one; each call becomes an
    integration_error alert via the executor."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str) -> Callable[..., Any]:
        def _call(*_a: Any, **_k: Any) -> Any:
            raise ToolUnsupported(f"{self._name}.{attr} not wired yet")

        return _call


def default_deps(settings: Settings) -> Deps:
    if settings.tool_name != "instantly":
        raise ValueError(f"no adapter for tool {settings.tool_name!r} yet")
    return Deps(
        tool=InstantlyTool(settings.tool_api_key, settings.tool_webhook_secret),
        wa=WhatsAppSender(WhatsAppClient(settings), settings),
        alerter=TeamsAlerter(settings.teams_webhook_url),
        outreach=_NotWired("outreach"),  # Task 12
        classify=make_classifier(settings) if settings.anthropic_api_key else None,
        bot_reply=make_bot(settings) if settings.anthropic_api_key else None,
    )


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
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != BOOKING_LEAD_PARAM]
    query.append((BOOKING_LEAD_PARAM, lead_token(lead_id, settings=settings)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _client_ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first[:IP_MAX]
    return request.client.host[:IP_MAX] if request.client else None


def create_app(settings: Optional[Settings] = None, deps: Optional[Deps] = None, engine: Any = None, *,
               clock: Callable[[], datetime] = _utcnow, scanners: Optional[Scanners] = None,
               verifier: Any = None) -> FastAPI:
    settings = settings or get_settings()
    if engine is None:
        engine = make_engine(settings.database_url)
    init_db(engine)  # idempotent create_all
    deps = deps or default_deps(settings)
    scanners = scanners or load_scanners()
    verifier = verifier or EmailVerifier(settings.zerobounce_api_key)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    home = settings.link_targets.get("homepage") or DEFAULT_HOME
    if deps.intake is None:
        # referrals from "wrong person" replies go through the same intake as POST /intake
        def _referral_intake(session: Session, payload: Dict[str, Any], now: datetime) -> Any:
            return intake_lead(session, payload, verifier, now, primary_when_both=settings.primary_when_both,
                               eu_email_approved=settings.eu_email_approved)

        deps.intake = _referral_intake
    if deps.handoff is None:
        deps.handoff = make_handoff(deps.alerter)
    if deps.takeover is None:
        def _takeover(session: Session, lead: Lead, reason: str, now: datetime) -> None:
            start_takeover(session, lead, reason, deps, now)

        deps.takeover = _takeover
    teams_key = settings.teams_outgoing_key
    teams_allowed = settings.teams_allowed_aad_id_set
    teams_tenant = settings.teams_tenant_id.strip().lower()  # GUID: case-insensitive
    teams_channel = settings.teams_sales_channel_id.strip()

    app = FastAPI(title="Ozoo outreach agent", docs_url=None, redoc_url=None, openapi_url=None)

    def get_session() -> Iterator[Session]:
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled %s on %s", type(exc).__name__, request.url.path.split("/")[1:2])
        return _fail(500)

    @app.get("/healthz")
    def healthz() -> Dict[str, bool]:
        return {"ok": True}

    # ------------------------------------------------------------ email webhook
    @app.post("/webhooks/email")
    async def email_webhook(request: Request, session: Session = Depends(get_session)) -> Response:
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
        except ValueError:
            logger.warning("email webhook rejected: malformed body")
            return _fail(400)
        try:
            await run_in_threadpool(ingest, session, events, deps, now, scanners)
        except Exception as exc:  # noqa: BLE001 - provider retries; idempotency makes it safe
            logger.error("email webhook ingest failed: %s", type(exc).__name__)
            return _fail(500)
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
    async def whatsapp_webhook(request: Request, session: Session = Depends(get_session)) -> Response:
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
        except ValueError:
            logger.warning("whatsapp webhook rejected: malformed body")
            return _fail(400)
        try:
            await run_in_threadpool(ingest, session, events, deps, clock(), scanners)
        except Exception as exc:  # noqa: BLE001 - Meta retries; idempotency makes it safe
            logger.error("whatsapp webhook ingest failed: %s", type(exc).__name__)
            return _fail(500)
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------ Cal.com webhook
    @app.post("/webhooks/cal")
    async def cal_webhook(request: Request, session: Session = Depends(get_session)) -> Response:
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
        except ValueError:
            logger.warning("cal webhook rejected: malformed body")
            return _fail(400)
        try:
            await run_in_threadpool(ingest, session, events, deps, now, scanners)
        except Exception as exc:  # noqa: BLE001 - Cal.com may retry; idempotency makes it safe
            logger.error("cal webhook ingest failed: %s", type(exc).__name__)
            return _fail(500)
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------ tracked links
    @app.get("/r/{token}")
    def redirect(token: str, request: Request, session: Session = Depends(get_session)) -> Response:
        parsed = parse_token(token, settings=settings)
        if parsed is None:
            return RedirectResponse(home, status_code=302, headers=NO_STORE)
        lead_id, step, tag, channel = parsed
        now = clock()
        try:
            exists = session.get(Lead, lead_id) is not None
            ua = request.headers.get("user-agent")
            session.add(Event(
                lead_id=lead_id if exists else None, channel=channel, kind="click", tag=tag, step=step,
                occurred_at=now, received_at=now, ip=_client_ip(request),
                user_agent=ua[:UA_MAX] if ua else None,
                # unsubscribe clicks are never acted on (ruling 9); others wait for the worker
                processed=tag == "unsubscribe", data={},
            ))
            session.commit()
            logger.info("click lead %s step %s tag %s", lead_id, step, tag)
        except Exception as exc:  # noqa: BLE001 - the visitor must always be redirected
            session.rollback()
            logger.error("click store failed for lead %s: %s", lead_id, type(exc).__name__)
        if tag == "unsubscribe":
            target = f"/u/{token}"
        elif tag == "booking" and settings.link_targets.get("booking"):
            target = with_lead_token(settings.link_targets["booking"], lead_id, settings)
        else:
            target = settings.link_targets.get(tag) or home
        return RedirectResponse(target, status_code=302, headers=NO_STORE)

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
                             eu_email_approved=settings.eu_email_approved)
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
        except (_BadRequest, ValueError):
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
