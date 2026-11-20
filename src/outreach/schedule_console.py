"""Web-app routes for the Email schedule screen and the lead panel's Email schedule tab (tasks 26-27). Local
only: they live under /demo/, which demo_console's middleware answers only on this computer.

No passcode (the product manager's call): every change is written to the lead's activity log
instead. Every POST must be JSON, so a cross-site form can't send one (no CORS preflight is granted).
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import sessionmaker

from . import send_plan as sp
from .models import Lead
from .sequencer import _live_templates, choose_version, render_version
from .template_library import sample_lead

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).with_name("demo_schedule.js")
NO_STORE = {"Cache-Control": "no-store"}


async def _json(request: Request) -> Dict[str, Any]:
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise sp.PlanError("Send JSON.")
    try:
        body = await request.json()
    except ValueError:
        raise sp.PlanError("Send JSON.") from None
    if not isinstance(body, dict):
        raise sp.PlanError("Send a JSON object.")
    return body


def _fail(exc: Exception) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


def preview(session: Any, settings: Any, step: Any, kind: Any, role: str) -> Dict[str, str]:
    """Email `step` of `kind` as a lead with `role` would get it (real page links, no tracking)."""
    try:
        step = int(step)
    except (TypeError, ValueError):
        raise sp.PlanError("Unknown email.") from None
    if not 1 <= step <= sp.LAST_STEP or kind not in sp.KINDS:
        raise sp.PlanError("Unknown email.")
    variant = "standard" if kind == "auto" else kind
    t = _live_templates(session)
    lead = sample_lead(role, "Asha Rao", "Acme Finance", variant)
    out = render_version(lead, choose_version(lead, step, t, variant), step, templates=t, settings=settings,
                         preview=True)
    out["html"] = out["html"].replace(settings.link_base_url.rstrip("/") + "/img/", "/img/")
    return out


def register(app: FastAPI, session_factory: sessionmaker, clock: Callable[[], datetime], settings: Any,
             tool: Optional[Any]) -> None:
    supported = bool(getattr(tool, "SUPPORTS_SCHEDULE", False))

    @app.get("/demo/schedule.js", include_in_schema=False)
    def schedule_script() -> Response:
        return Response(SCRIPT.read_text(encoding="utf-8"), media_type="text/javascript", headers=NO_STORE)

    @app.get("/demo/api/plans", include_in_schema=False)
    def plans_list() -> JSONResponse:
        with session_factory() as s:
            current = sp.view(s)
        return JSONResponse({**current, "options": sp.options(), "supported": supported,
                             "tz": settings.business_tz, "email_mode": settings.email_mode}, headers=NO_STORE)

    @app.post("/demo/api/plans/save", include_in_schema=False)
    async def plans_save(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            with session_factory() as s:
                row = sp.save_plan(s, body, clock())
                s.commit()
                out = sp.plan_row(row)
        except sp.PlanError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, "plan": out})

    @app.post("/demo/api/plans/preview", include_in_schema=False)
    async def plans_preview(request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            with session_factory() as s:
                out = preview(s, settings, body.get("step"), body.get("kind"), str(body.get("role") or ""))
        except sp.PlanError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, **out})

    @app.get("/demo/api/schedule/upcoming", include_in_schema=False)
    def schedule_upcoming() -> JSONResponse:
        with session_factory() as s:
            items = sp.upcoming(s, settings)
        return JSONResponse({"items": items, "tz": settings.business_tz}, headers=NO_STORE)

    @app.get("/demo/api/lead/{lead_id}/schedule", include_in_schema=False)
    def lead_schedule(lead_id: int) -> JSONResponse:
        with session_factory() as s:
            lead = s.get(Lead, lead_id)
            if lead is None:
                return JSONResponse({"error": "not found"}, status_code=404)
            view = sp.lead_view(s, lead, settings)
            saved = sp.current(s) is not None
        return JSONResponse({**view, "schedule_saved": saved, "supported": supported}, headers=NO_STORE)

    @app.post("/demo/api/lead/{lead_id}/schedule", include_in_schema=False)
    async def lead_schedule_change(lead_id: int, request: Request) -> JSONResponse:
        try:
            body = await _json(request)
            if tool is None:
                raise sp.PlanError("No email sender is configured.")
            with session_factory() as s:
                lead = s.get(Lead, lead_id)
                if lead is None:
                    raise sp.PlanError("Lead not found.")
                what = sp.change_next(s, lead, tool, str(body.get("action") or ""), clock(), settings,
                                      at=body.get("at") or None, days=body.get("days"), kind=body.get("kind"))
                s.commit()
        except sp.PlanError as exc:
            return _fail(exc)
        logger.info("lead %s: schedule changed (%s)", lead_id, body.get("action"))
        return JSONResponse({"ok": True, "what": what})
