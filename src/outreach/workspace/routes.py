"""Read-only JSON routes for the Connectors and Settings screens. Local only: they live under /demo/,
which demo_console's middleware answers only on this computer."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from .connectors import connectors
from .settings_view import settings_view

NO_STORE = {"Cache-Control": "no-store"}


def register(app: FastAPI, session_factory: sessionmaker, clock: Callable[[], datetime], settings: Any) -> None:
    @app.get("/demo/api/connectors", include_in_schema=False)
    def connectors_list() -> JSONResponse:
        with session_factory() as s:
            out = connectors(settings, s, clock())
        return JSONResponse({"connectors": out}, headers=NO_STORE)

    @app.get("/demo/api/settings", include_in_schema=False)
    def settings_get() -> JSONResponse:
        return JSONResponse(settings_view(settings), headers=NO_STORE)
