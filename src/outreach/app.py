"""FastAPI app: email webhook, tracked-link redirect, scanner-safe unsubscribe, intake, health."""
from __future__ import annotations

import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from .actions import Deps
from .alerts import SlackAlerter
from .botfilter import Scanners, load_scanners
from .classifier import make_classifier
from .config import Settings, get_settings
from .db import init_db, make_engine
from .emailtool.base import BadSignature, NormalizedEvent, ToolUnsupported
from .emailtool.instantly import InstantlyTool
from .ingest import IP_MAX, UA_MAX, ingest
from .intake import intake_lead
from .links import parse_token
from .models import Event, Lead
from .unsubscribe_page import CONFIRM_HTML, DONE_HTML, ERROR_HTML, HEADERS as PAGE_HEADERS, NOT_FOUND_HTML
from .verify import EmailVerifier
from .whatsapp.client import WhatsAppClient
from .whatsapp.sender import WhatsAppSender
from .whatsapp.webhook import parse_wa_webhook

logger = logging.getLogger(__name__)

MAX_BODY = 1024 * 1024  # 1 MiB
MAX_CHALLENGE_LEN = 64
DEFAULT_HOME = "https://ozoo.ai/"
NO_STORE = {"Cache-Control": "no-store"}


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
    )


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

    return app
