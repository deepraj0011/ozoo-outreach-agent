"""Web-app routes for the Templates screen (task 22). Local only: they live under /demo/, which
demo_console's middleware answers only on this computer (never through a tunnel).

Every POST must be JSON (application/json): a cross-site form or "simple" request can't send that
without a CORS preflight, which this app never grants. Approving, switching off and sending also
need the approver passcode (template_library.check_passcode).
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import template_library as lib
from .models import Lead

logger = logging.getLogger(__name__)

MAX_LEADS_LISTED = 500
SCRIPT = Path(__file__).with_name("demo_templates.js")


async def _json(request: Request) -> Dict[str, Any]:
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise lib.TemplateError("Send JSON.")
    try:
        body = await request.json()
    except ValueError:
        raise lib.TemplateError("Send JSON.") from None
    if not isinstance(body, dict):
        raise lib.TemplateError("Send a JSON object.")
    return body


def _fail(exc: Exception) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


def _lead_row(session: Any, lead: Lead) -> Dict[str, Any]:
    return {"id": lead.id, "name": lead.name, "company": lead.company, "role": lead.role or "",
            "state": lead.state, "step_sent": lead.step_sent, "reason": lib.eligibility(session, lead)}


def register(app: FastAPI, session_factory: sessionmaker, clock: Callable[[], datetime], settings: Any,
             tool: Optional[Any]) -> None:

    @app.get("/demo/templates.js", include_in_schema=False)
    def templates_script() -> Response:
        return Response(SCRIPT.read_text(encoding="utf-8"), media_type="text/javascript",
                        headers={"Cache-Control": "no-store"})

    @app.get("/demo/api/templates", include_in_schema=False)
    def templates_list() -> JSONResponse:
        with session_factory() as s:
            items = lib.list_templates(s)
            leads = list(s.scalars(select(Lead).order_by(Lead.id.desc()).limit(MAX_LEADS_LISTED)))
            rows = [_lead_row(s, ld) for ld in leads]
        approver = bool(getattr(settings, "template_approver_passcode", None))
        return JSONResponse({"templates": items, "options": lib.options(), "leads": rows,
                             "approver_set": approver, "can_send": tool is not None},
                            headers={"Cache-Control": "no-store"})

    @app.post("/demo/api/templates/preview", include_in_schema=False)
    async def templates_preview(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            sample = body.get("sample") or {}
            with session_factory() as s:
                out = lib.preview(s, settings, key=body.get("key"), draft=body.get("draft"),
                                  role=str(sample.get("role") or ""), name=str(sample.get("name") or "Asha Rao"),
                                  company=str(sample.get("company") or "Acme Finance"))
            # images from this app (the tracking host may be a tunnel that is down while editing)
            out["html"] = out["html"].replace(settings.link_base_url.rstrip("/") + "/img/", "/img/")
        except lib.TemplateError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, **out})

    @app.post("/demo/api/templates/save", include_in_schema=False)
    async def templates_save(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            with session_factory() as s:
                row = lib.save_template(s, body.get("draft"), str(body.get("by") or ""), clock(), settings=settings,
                                        key=body.get("key") or None)
                s.commit()
                key = row.key
        except lib.TemplateError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, "key": key})

    @app.post("/demo/api/templates/status", include_in_schema=False)
    async def templates_status(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            with session_factory() as s:
                item = lib.set_status(s, str(body.get("key") or ""), str(body.get("status") or ""),
                                      str(body.get("by") or ""), str(body.get("passcode") or ""), settings, clock())
                s.commit()
        except lib.TemplateError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, "template": item})

    @app.post("/demo/api/templates/send", include_in_schema=False)
    async def templates_send(request: Request) -> JSONResponse:
        try:
            if tool is None:
                raise lib.TemplateError("No email tool is configured, so nothing can be sent.")
            body = await _json(request)
            lead_ids = body.get("lead_ids")
            if not isinstance(lead_ids, list):
                raise lib.TemplateError("Choose leads from the list.")
            with session_factory() as s:
                results = lib.send_manual(s, str(body.get("key") or ""), lead_ids, by=str(body.get("by") or ""),
                                          passcode=str(body.get("passcode") or ""), settings=settings, tool=tool,
                                          now=clock())
                s.commit()
        except lib.TemplateError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, "results": results})
