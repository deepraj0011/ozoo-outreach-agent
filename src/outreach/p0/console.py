"""JSON routes for the P0 screens (Templates, Email schedule, lead Emails tab). Local only: they live under
/demo/, which demo_console's middleware answers only on this computer. Every POST must be JSON, so a
cross-site form can't send one. All logic is in p0.templates / p0.schedule / p0.flow; these only translate."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import sessionmaker

from ..models import Lead
from . import board as brd
from . import flow, planner
from . import schedule as sch
from . import templates as tpls
from .templates import P0Error

logger = logging.getLogger(__name__)

SCRIPT = Path(__file__).with_name("console.js")
NO_STORE = {"Cache-Control": "no-store"}
ACTIONS = ("pause", "resume", "stop", "send_now")


class _NotJson(Exception):
    pass


def _int_or_none(raw: Any, what: str) -> Any:
    """A template id from the screen: absent, or a whole number (P0Error otherwise)."""
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise P0Error(f"The {what} must be a whole number.")
    return raw


async def _json(request: Request) -> Dict[str, Any]:
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise _NotJson()
    try:
        body = await request.json()
    except ValueError:
        raise _NotJson() from None
    if not isinstance(body, dict):
        raise _NotJson()
    return body


def register(app: FastAPI, session_factory: sessionmaker, clock: Callable[[], datetime], settings: Any,
             deps: Any) -> None:
    def on() -> bool:
        return flow.is_p0(settings, deps.tool)

    def guarded(fn: Callable[[Dict[str, Any]], Any]) -> Callable[..., Any]:
        async def handler(request: Request) -> JSONResponse:
            try:
                body = await _json(request)
            except _NotJson:
                return JSONResponse({"error": "Send JSON."}, status_code=415)
            return _run(fn, body)
        return handler

    def _run(fn: Callable[[Dict[str, Any]], Any], body: Dict[str, Any]) -> JSONResponse:
        try:
            with session_factory() as s:
                out = fn({**body, "_s": s})
                s.commit()
        except P0Error as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(out)

    @app.get("/demo/p0.js", include_in_schema=False)
    def script() -> Response:
        return Response(SCRIPT.read_text(encoding="utf-8"), media_type="text/javascript", headers=NO_STORE)

    @app.get("/demo/api/p0/status", include_in_schema=False)
    def status() -> JSONResponse:
        return JSONResponse({"on": on()}, headers=NO_STORE)

    @app.get("/demo/api/p0/templates", include_in_schema=False)
    def templates_list() -> JSONResponse:
        with session_factory() as s:
            tpls.ensure_starter(s, clock(), settings=settings)
            s.commit()
            out = {"templates": tpls.list_templates(s), "placeholders": list(tpls.PLACEHOLDERS)}
        return JSONResponse(out, headers=NO_STORE)

    def _save_template(b: Dict[str, Any]) -> Dict[str, Any]:
        s = b.pop("_s")
        _int_or_none(b.get("id"), "template id")
        tpl = tpls.save_template(s, b, str(b.get("by") or ""), clock(), settings=settings)
        return {"ok": True, "id": tpl.id}

    app.post("/demo/api/p0/templates", include_in_schema=False)(guarded(_save_template))

    @app.post("/demo/api/p0/templates/{template_id}/status", include_in_schema=False)
    async def template_status(template_id: int, request: Request) -> JSONResponse:
        try:
            body = await _json(request)
        except _NotJson:
            return JSONResponse({"error": "Send JSON."}, status_code=415)
        return _run(lambda b: (tpls.set_status(b["_s"], template_id, str(b.get("status") or ""),
                                               str(b.get("by") or ""), clock()), {"ok": True})[1], body)

    def _preview(b: Dict[str, Any]) -> Dict[str, Any]:
        tid = _int_or_none(b.get("template_id"), "template id")
        out = tpls.preview(b["_s"], settings, template_id=tid, draft=b.get("draft"),
                           role=str(b.get("role") or "Head of Collections"))
        out["html"] = out["html"].replace(settings.link_base_url.rstrip("/") + "/img/", "/img/")
        return {"subject": out["subject"], "text": out["text"], "html": out["html"]}

    app.post("/demo/api/p0/preview", include_in_schema=False)(guarded(_preview))

    @app.get("/demo/api/p0/schedule", include_in_schema=False)
    def schedule_get() -> JSONResponse:
        with session_factory() as s:
            tpls.ensure_starter(s, clock(), settings=settings)
            s.commit()
            out = {"rows": sch.get_schedule(s), "example": sch.example_dates(s, clock(), settings=settings)}
        return JSONResponse(out, headers=NO_STORE)

    def _save_schedule(b: Dict[str, Any]) -> Dict[str, Any]:
        s = b["_s"]
        rows = b.get("rows")
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise P0Error("Send the schedule as a list of emails.")
        saved = sch.save_schedule(s, rows, str(b.get("by") or ""), clock(), settings=settings)
        return {"rows": saved, "example": sch.example_dates(s, clock(), settings=settings)}

    app.post("/demo/api/p0/schedule", include_in_schema=False)(guarded(_save_schedule))

    def _example(b: Dict[str, Any]) -> Dict[str, Any]:
        rows = b.get("rows")
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise P0Error("Send the schedule as a list of emails.")
        days = [r.get("day") for r in rows]
        return {"example": sch.example_dates(b["_s"], clock(), settings=settings, days=days)}

    app.post("/demo/api/p0/schedule/example", include_in_schema=False)(guarded(_example))

    @app.get("/demo/api/p0/board", include_in_schema=False)
    def board_view() -> JSONResponse:
        with session_factory() as s:
            tpls.ensure_starter(s, clock(), settings=settings)
            s.commit()
            out = brd.board(s, clock(), settings=settings)
        return JSONResponse(out, headers=NO_STORE)

    @app.get("/demo/api/p0/leads/{lead_id}/emails/{email_no}", include_in_schema=False)
    def email_view(lead_id: int, email_no: int) -> JSONResponse:
        with session_factory() as s:
            try:
                out = brd.email_detail(s, lead_id, email_no, clock(), settings=settings)
            except P0Error as exc:
                return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(out, headers=NO_STORE)

    @app.get("/demo/api/p0/leads/{lead_id}", include_in_schema=False)
    def lead_rows(lead_id: int) -> JSONResponse:
        with session_factory() as s:
            lead = s.get(Lead, lead_id)
            if lead is None:
                return JSONResponse({"error": "not found"}, status_code=404)
            out = {"rows": planner.lead_view(s, lead, clock(), settings=settings),
                   "paused": bool((lead.variant_flags or {}).get(flow.PAUSED_FLAG))}
        return JSONResponse(out, headers=NO_STORE)

    @app.post("/demo/api/p0/leads/{lead_id}/{action}", include_in_schema=False)
    async def lead_action(lead_id: int, action: str, request: Request) -> JSONResponse:
        try:
            body = await _json(request)
        except _NotJson:
            return JSONResponse({"error": "Send JSON."}, status_code=415)
        if action not in ACTIONS:
            return JSONResponse({"error": "Unknown action."}, status_code=404)

        def run(b: Dict[str, Any]) -> Dict[str, Any]:
            s, by, now = b["_s"], str(b.get("by") or ""), clock()
            tpls._who(by)
            lead = s.get(Lead, lead_id)
            if lead is None:
                raise P0Error("Lead not found.")
            if not on():
                raise P0Error("The P0 email flow is not on.")
            sent = None
            if action == "pause":
                flow.pause(s, lead, deps.tool, by, now)
            elif action == "resume":
                flow.resume(s, lead, deps.tool, by, now, settings=settings)
            elif action == "stop":
                flow.stop(s, lead, deps, by, now)
            else:
                sent = flow.send_now(s, lead, deps.tool, by, now, settings=settings)
            return {"ok": True, "sent": sent}

        return _run(run, body)
