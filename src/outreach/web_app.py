"""The web app (plain HTML + CSS + JS modules in src/outreach/web/) served at /app/. Local only: the
demo console's middleware answers /app only on this computer, like /demo. The pages use the /demo/api JSON
routes; there is no login yet, so this must never be reachable from outside."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

WEB_DIR = Path(__file__).with_name("web")


class _RevalidatedFiles(StaticFiles):
    """Static files the browser must re-check each load, so an edited module is never served stale."""

    async def get_response(self, path: str, scope: Any) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def register(app: FastAPI) -> None:
    app.mount("/app", _RevalidatedFiles(directory=str(WEB_DIR), html=True), name="web_app")
