"""The web app (React + MUI, built from frontend/ into src/outreach/webapp/) served at /app/. Local only: the
demo console's middleware answers /app only on this computer, like /demo. The pages use the /demo/api JSON
routes; there is no login yet, so this must never be reachable from outside."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.responses import FileResponse, HTMLResponse, RedirectResponse, Response

WEB_DIR = Path(__file__).with_name("webapp")
NO_CACHE = {"Cache-Control": "no-cache"}
NOT_BUILT_HTML = (
    "<!doctype html><title>Ozoo.ai Outreach</title>"
    "<p>The web app is not built yet. Run scripts/build_web, then reload.</p>"
)


def register(app: FastAPI, web_dir: Path = WEB_DIR) -> None:
    root = web_dir.resolve()

    @app.get("/app", include_in_schema=False)
    async def app_no_slash() -> Response:
        return RedirectResponse("/app/")

    @app.get("/app/{path:path}", include_in_schema=False)
    async def app_files(path: str) -> Response:
        index = root / "index.html"
        if not index.is_file():
            return HTMLResponse(NOT_BUILT_HTML, status_code=503, headers=NO_CACHE)
        try:
            candidate = (root / path).resolve()
            is_file = bool(path) and candidate.is_file() and root in candidate.parents
        except (ValueError, OSError):
            return Response(status_code=404, headers=NO_CACHE)
        if is_file:
            return FileResponse(candidate, headers=NO_CACHE)
        if path.startswith("assets/"):
            return Response(status_code=404, headers=NO_CACHE)  # a missing bundle must not look like the page
        return FileResponse(index, headers=NO_CACHE)
