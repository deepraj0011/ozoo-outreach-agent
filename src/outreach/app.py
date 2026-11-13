"""FastAPI app: email + WhatsApp webhooks, tracked-link redirect, scanner-safe unsubscribe, intake,
Slack slash commands (/wa, /wa-done), health."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from .actions import Deps
from .alerts import SlackAlerter
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
from .links import parse_token
from .models import Event, Lead
from .unsubscribe_page import CONFIRM_HTML, DONE_HTML, ERROR_HTML, HEADERS as PAGE_HEADERS, NOT_FOUND_HTML
from .verify import EmailVerifier
from .whatsapp.client import WaSendError, WhatsAppClient, WhatsAppNotAllowed, WindowClosed
from .whatsapp.sender import WhatsAppSender
from .whatsapp.webhook import parse_wa_webhook

logger = logging.getLogger(__name__)

MAX_BODY = 1024 * 1024  # 1 MiB
MAX_CHALLENGE_LEN = 64
DEFAULT_HOME = "https://ozoo.ai/"
NO_STORE = {"Cache-Control": "no-store"}
SLACK_MAX_BODY = 64 * 1024
SLACK_MAX_AGE_S = 300
SLACK_TS_MAX_LEN = 12
WA_TEXT_MAX = 4096
OWNER_MAX = 100  # Lead.wa_human_owner column size
WA_USAGE = "usage: /wa <lead_id> <message>"
WA_DONE_USAGE = "usage: /wa-done <lead_id>"


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
        alerter=SlackAlerter(settings.slack_webhook_url),
        outreach=_NotWired("outreach"),  # Task 12
        classify=make_classifier(settings) if settings.anthropic_api_key else None,
        bot_reply=make_bot(settings) if settings.anthropic_api_key else None,
    )


def verify_slack_signature(headers: Mapping[str, str], body: bytes, secret: str, now: datetime) -> bool:
    """Slack v0 request signing: HMAC-SHA256 over "v0:<ts>:<raw body>", timestamp within 5 min."""
    sig = headers.get("x-slack-signature") or ""
    ts = headers.get("x-slack-request-timestamp") or ""
    if not sig or not ts or len(ts) > SLACK_TS_MAX_LEN or not (ts.isascii() and ts.isdigit()):
        return False
    if abs(now.timestamp() - int(ts)) > SLACK_MAX_AGE_S:
        return False
    expected = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), sig.encode())


def _lead_id_arg(raw: str) -> Optional[int]:
    return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 18 else None


def _first(form: Dict[str, List[str]], key: str) -> str:
    vals = form.get(key) or [""]
    return vals[0]


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
    if deps.takeover is None:
        def _takeover(session: Session, lead: Lead, reason: str, now: datetime) -> None:
            start_takeover(session, lead, reason, deps, now)

        deps.takeover = _takeover
    allowed_users = frozenset(u.strip() for u in settings.slack_allowed_user_ids.split(",") if u.strip())

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

    # ------------------------------------------------------------ Slack slash commands
    def _cmd_wa(session: Session, text: str, user_id: str, now: datetime) -> str:
        parts = text.strip().split(None, 1)
        lead_id = _lead_id_arg(parts[0]) if parts else None
        if len(parts) != 2 or lead_id is None:
            return WA_USAGE
        message = parts[1]
        if not 1 <= len(message) <= WA_TEXT_MAX:
            return f"message must be 1-{WA_TEXT_MAX} characters"
        lead = session.get(Lead, lead_id)
        if lead is None:
            return "lead not found"
        try:
            deps.wa.send_raw_text(session, lead, message, now, "human")
        except WindowClosed:
            session.rollback()
            outcome = "24h window closed — the lead must message first"
        except WhatsAppNotAllowed:
            session.rollback()
            outcome = "lead has opted out / no WhatsApp consent"
        except WaSendError as exc:
            session.rollback()
            outcome = f"send failed (code {exc.code})"
        else:
            lead.wa_human_owner = (user_id or "slack")[:OWNER_MAX]
            session.commit()
            logger.info("slack /wa: lead %s message sent by a person", lead_id)
            return "sent"
        logger.info("slack /wa: lead %s not sent", lead_id)
        return outcome

    def _cmd_wa_done(session: Session, text: str, now: datetime) -> str:
        lead_id = _lead_id_arg(text.strip())
        if lead_id is None:
            return WA_DONE_USAGE
        lead = session.get(Lead, lead_id)
        if lead is None:
            return "lead not found"
        lead.wa_human_owner = None
        flags = dict(lead.variant_flags or {})
        flags["takeover_released"] = now.isoformat()
        lead.variant_flags = flags
        session.commit()
        logger.info("slack /wa-done: lead %s given back to the bot", lead_id)
        return "released"

    def _slash(session: Session, command: str, text: str, user_id: str, now: datetime) -> str:
        if command == "/wa":
            return _cmd_wa(session, text, user_id, now)
        if command == "/wa-done":
            return _cmd_wa_done(session, text, now)
        return "unknown command"

    @app.post("/slack/commands")
    async def slack_commands(request: Request, session: Session = Depends(get_session)) -> Response:
        try:
            body = await _read_capped(request, SLACK_MAX_BODY)
        except _BodyTooLarge:
            return _fail(413)
        except _BadRequest:
            return _fail(400)
        now = clock()
        if not verify_slack_signature(request.headers, body, settings.slack_signing_secret, now):
            logger.warning("slack command rejected: bad signature or timestamp")
            return _fail(401)
        try:
            form = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=50)
        except (UnicodeDecodeError, ValueError):
            return _fail(400)
        user_id = _first(form, "user_id")
        if allowed_users and user_id not in allowed_users:
            logger.warning("slack command refused: user not allowed")
            return _ephemeral("not allowed")
        try:
            text = await run_in_threadpool(_slash, session, _first(form, "command"), _first(form, "text"),
                                           user_id, now)
        except Exception as exc:  # noqa: BLE001 - Slack still needs a 200 with a readable answer
            session.rollback()
            logger.error("slack command failed: %s", type(exc).__name__)
            text = "internal error — nothing was changed; try again"
        return _ephemeral(text)

    return app


def _ephemeral(text: str) -> JSONResponse:
    """Slack needs a 200 within 3 s; ephemeral = only the person who typed the command sees it."""
    return JSONResponse({"response_type": "ephemeral", "text": text})
